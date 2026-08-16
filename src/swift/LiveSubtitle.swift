import AppKit
import AVFoundation
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit
import Speech

/// A capture channel, plus the one pane that is fed by both of them: `auto` carries whichever
/// channel AudioGate picked, and every line it shows says which channel that was.
enum Source: String, CaseIterable {
    case mic
    case sys
    case auto
}

enum SourceMode: String {
    case mic
    case system
    case auto

    /// `both` used to open one recognizer per channel in two side-by-side columns. The caption
    /// window is single-pane now, so the retired value resolves to `auto` rather than failing a
    /// config file or a habit that predates the change.
    static func parse(_ value: String) -> SourceMode? {
        value == "both" ? .auto : SourceMode(rawValue: value)
    }

    /// The channels to capture. `auto` records both and lets AudioGate pick between them.
    var sources: [Source] {
        switch self {
        case .mic: return [.mic]
        case .system: return [.sys]
        case .auto: return [.mic, .sys]
        }
    }

    /// The single pane the caption window shows for this mode.
    var pane: Source {
        switch self {
        case .mic: return .mic
        case .system: return .sys
        case .auto: return .auto
        }
    }
}

/// How a caption names the channel it came from; also the tag auto's transcript lines carry.
/// The `auto` pane is not a channel, so it contributes nothing of its own.
func sourceLabel(_ source: Source) -> String {
    switch source {
    case .mic: return "(microphone) "
    case .sys: return "(speaker) "
    case .auto: return ""
    }
}

enum ASRMode: String {
    case apple
    case hf
    case hfStream = "hf-stream"
    case sherpa
}

/// One entry of the caption bar's model dropdown: a backend plus, for Hugging Face, its model id.
struct ASRChoice: Equatable {
    let mode: ASRMode
    let hfModel: String

    init(mode: ASRMode, hfModel: String = "") {
        self.mode = mode
        self.hfModel = mode == .hf || mode == .hfStream ? hfModel : ""
    }

    var isHF: Bool { mode == .hf || mode == .hfStream }

    var id: String { isHF ? "\(mode.rawValue):\(hfModel)" : mode.rawValue }

    var menuTitle: String {
        switch mode {
        case .apple: return "Apple Speech"
        case .sherpa: return "Sherpa-ONNX"
        case .hf: return "\(hfModel) (chunked)"
        case .hfStream: return "\(hfModel) (streaming)"
        }
    }

    /// Short enough for the 108pt button; the full id stays in the tooltip and the menu.
    var buttonTitle: String {
        switch mode {
        case .apple: return "Apple"
        case .sherpa: return "Sherpa"
        case .hf, .hfStream:
            let name = hfModel.split(separator: "/").last.map(String.init) ?? "HF"
            return name.count <= 16 ? name : String(name.prefix(15)) + "…"
        }
    }
}

/// Split one model spec into a path plus a model id.
///
/// A cache-aware checkpoint decoded in fixed blocks throws away the thing it was built for, and a
/// plain seq2seq has no streaming state to keep, so the two never share a worker. `stream:` and
/// `offline:` pick the path outright; otherwise the id does, since checkpoints that stream say so
/// in their name (nvidia/nemotron-3.5-asr-streaming-0.6b).
func hfChoice(_ spec: String) -> ASRChoice {
    for (prefix, mode) in [("stream:", ASRMode.hfStream), ("offline:", ASRMode.hf)] where spec.hasPrefix(prefix) {
        return ASRChoice(mode: mode,
                         hfModel: String(spec.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces))
    }
    return ASRChoice(mode: spec.lowercased().contains("streaming") ? .hfStream : .hf, hfModel: spec)
}

struct Config {
    var sourceMode: SourceMode = .auto
    var asrMode: ASRMode = .apple
    var language = "zh-CN"
    var outputDir = "transcripts"
    var opacity: CGFloat = 0.75
    var height: CGFloat = 120
    var hfModel: String?
    var hfModels: [String] = []
    var hfScript: String
    var hfStreamScript: String
    /// Streaming checkpoints need transformers >= 5.13, which qwen-asr (pinned at 4.57) refuses to
    /// share, so the streaming worker may have to run out of a second environment.
    var hfStreamPython = "python3"
    var sherpaScript: String
    var stopScript: String
    var debug = false
    var debugDir: String
    var record = false
    var recordDir: String

    /// The one pane the window shows. It never changes: the ASR model can be swapped mid-run
    /// without the layout moving under the captions.
    var pane: Source { sourceMode.pane }

    var currentChoice: ASRChoice { ASRChoice(mode: asrMode, hfModel: hfModel ?? "") }

    /// hfModels already leads with the startup model, prefixed by parseArgs so --asr keeps its say.
    var choices: [ASRChoice] {
        var list = [ASRChoice(mode: .apple), ASRChoice(mode: .sherpa)]
        var seen = Set<String>()
        for spec in hfModels {
            let choice = hfChoice(spec)
            if !choice.hfModel.isEmpty, seen.insert(choice.id).inserted { list.append(choice) }
        }
        return list
    }
}

/// Apple Speech needs a real locale, so the loose spellings collapse onto one.
///
/// # ponytail: applied where SFSpeechRecognizer is built, not at parse time -- config.language has
/// to stay as typed for the Python workers, and "auto" means detect-per-utterance to a streaming
/// checkpoint but has no such thing on Apple Speech.
func localeID(_ language: String) -> String {
    switch language.lowercased() {
    case "zh", "cn", "chinese", "auto", "mixed", "zh+en", "en+zh":
        return "zh-CN"
    case "en", "english":
        return "en-US"
    default:
        return language
    }
}

func parseArgs() -> Config {
    let scriptDir = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
    let projectDir = scriptDir.deletingLastPathComponent()
    var config = Config(
        outputDir: projectDir.appendingPathComponent("transcripts").path,
        hfScript: projectDir.appendingPathComponent("src/python/hf_asr_worker.py").path,
        hfStreamScript: projectDir.appendingPathComponent("src/python/hf_stream_worker.py").path,
        sherpaScript: projectDir.appendingPathComponent("src/python/sherpa_asr_worker.py").path,
        stopScript: projectDir.appendingPathComponent("scripts/stop.sh").path,
        debugDir: projectDir.appendingPathComponent("debug-audio").path,
        recordDir: projectDir.appendingPathComponent("recordings").path
    )

    var args = Array(CommandLine.arguments.dropFirst())
    while let arg = args.first {
        args.removeFirst()
        func value() -> String {
            guard let next = args.first else {
                fputs("Missing value for \(arg)\n", stderr)
                exit(2)
            }
            args.removeFirst()
            return next
        }

        switch arg {
        case "--source":
            guard let mode = SourceMode.parse(value()) else {
                fputs("Use --source mic|system|auto\n", stderr)
                exit(2)
            }
            config.sourceMode = mode
        case "--asr":
            guard let mode = ASRMode(rawValue: value()) else {
                fputs("Use --asr apple|hf|hf-stream|sherpa\n", stderr)
                exit(2)
            }
            config.asrMode = mode
        case "--language":
            config.language = value()
        case "--output-dir":
            config.outputDir = value()
        case "--opacity":
            config.opacity = CGFloat(Double(value()) ?? 0.75)
        case "--height":
            config.height = CGFloat(Double(value()) ?? 120)
        case "--hf-model":
            config.hfModel = value()
        case "--hf-models":
            config.hfModels = value()
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
        case "--hf-script":
            config.hfScript = value()
        case "--hf-stream-script":
            config.hfStreamScript = value()
        case "--hf-stream-python":
            config.hfStreamPython = value()
        case "--sherpa-script":
            config.sherpaScript = value()
        case "--debug":
            config.debug = true
        case "--record":
            config.record = true
        case "--record-dir":
            config.recordDir = value()
        case "--help", "-h":
            print("""
            Usage:
              live-subtitle --source mic|system|auto
              live-subtitle --asr apple --language zh-CN
              live-subtitle --asr hf --hf-model openai/whisper-small
              live-subtitle --asr hf-stream --hf-model nvidia/nemotron-3.5-asr-streaming-0.6b
              live-subtitle --asr sherpa
              live-subtitle --hf-models openai/whisper-small,stream:nvidia/nemotron-3.5-asr-streaming-0.6b
              live-subtitle --debug
              live-subtitle --record [--record-dir <dir>]
            """)
            exit(0)
        default:
            fputs("Unknown option: \(arg)\n", stderr)
            exit(2)
        }
    }

    config.opacity = min(1, max(0.1, config.opacity))
    config.height = min(500, max(70, config.height))

    let startup = ASRChoice(mode: config.asrMode, hfModel: config.hfModel ?? "")
    if startup.isHF && startup.hfModel.isEmpty {
        fputs("--asr \(config.asrMode.rawValue) requires --hf-model <huggingface/model-id>\n", stderr)
        exit(2)
    }
    // the startup model keeps the path --asr picked; the rest of the dropdown goes by its name
    if startup.isHF {
        config.hfModels.insert((startup.mode == .hfStream ? "stream:" : "offline:") + startup.hfModel, at: 0)
    } else if let model = config.hfModel, !model.isEmpty {
        config.hfModels.insert(model, at: 0)
    }

    return config
}

final class TranscriptWriter {
    private let outputDir: URL
    private var handles: [Source: FileHandle] = [:]
    private var dates: [Source: String] = [:]

    init(path: String) {
        outputDir = URL(fileURLWithPath: path)
        try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
    }

    /// One file per channel, except auto: that pane is a single conversation, so both channels
    /// share the main file and `speaker` puts the channel's name at the head of every line.
    ///
    /// `language` is whatever the worker detected for this line -- only --asr hf-stream on
    /// --language auto reports one, and it is what makes a bilingual meeting searchable later.
    func append(source: Source, text: String, speaker: Source? = nil, language: String = "") {
        let today = Self.dateFormatter.string(from: Date())
        if dates[source] != today {
            handles[source]?.closeFile()
            let suffix = source == .sys ? "-sys" : ""
            let file = outputDir.appendingPathComponent("\(today)\(suffix).txt")
            if !FileManager.default.fileExists(atPath: file.path) {
                FileManager.default.createFile(atPath: file.path, contents: nil)
            }
            handles[source] = try? FileHandle(forWritingTo: file)
            handles[source]?.seekToEndOfFile()
            dates[source] = today
        }
        let time = Self.timeFormatter.string(from: Date())
        let label = speaker.map(sourceLabel) ?? ""
        let tag = language.isEmpty ? "" : "[\(language)] "
        if let data = "[\(time)] \(label)\(tag)\(text)\n".data(using: .utf8) {
            handles[source]?.write(data)
        }
    }

    func close() {
        handles.values.forEach { $0.closeFile() }
    }

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    private static let timeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter
    }()
}

final class DebugRecorder {
    private let outputDir: URL
    private let runID = DebugRecorder.dateFormatter.string(from: Date())
    private let queue = DispatchQueue(label: "LiveCaption.debug-audio")
    private var handles: [Source: FileHandle] = [:]
    private var sampleRates: [Source: Double] = [:]
    private var dataSizes: [Source: UInt32] = [:]
    private var failedSources = Set<Source>()

    init(path: String) {
        outputDir = URL(fileURLWithPath: path)
        try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
    }

    func record(source: Source, sampleRate: Double, floats: [Float]) -> Float {
        let level = rmsDB(floats)
        guard !floats.isEmpty else { return level }
        queue.async { [self] in
            write(source: source, sampleRate: sampleRate, floats: floats)
        }
        return level
    }

    func close() {
        queue.sync {
            for (source, handle) in handles {
                updateHeader(handle: handle, sampleRate: sampleRates[source] ?? 16_000, dataSize: dataSizes[source] ?? 0)
                try? handle.close()
            }
            handles.removeAll()
            sampleRates.removeAll()
            dataSizes.removeAll()
        }
    }

    private func write(source: Source, sampleRate: Double, floats: [Float]) {
        guard !failedSources.contains(source) else { return }
        do {
            let handle = try fileHandle(for: source, sampleRate: sampleRate)
            var data = Data(capacity: floats.count * 2)
            for index in floats.indices {
                let sample = max(-1, min(1, floats[index]))
                var value = Int16(sample * Float(Int16.max)).littleEndian
                withUnsafeBytes(of: &value) { data.append(contentsOf: $0) }
            }
            handle.write(data)
            dataSizes[source, default: 0] += UInt32(data.count)
        } catch {
            failedSources.insert(source)
            fputs("Debug audio write failed for \(source.rawValue): \(error.localizedDescription)\n", stderr)
        }
    }

    private func fileHandle(for source: Source, sampleRate: Double) throws -> FileHandle {
        if let handle = handles[source] { return handle }
        let label = source == .mic ? "microphone" : "speaker"
        let file = outputDir.appendingPathComponent("\(runID)-\(label).wav")
        FileManager.default.createFile(atPath: file.path, contents: wavHeader(sampleRate: sampleRate, dataSize: 0))
        let handle = try FileHandle(forWritingTo: file)
        handle.seekToEndOfFile()
        handles[source] = handle
        sampleRates[source] = sampleRate
        dataSizes[source] = 0
        return handle
    }

    private func updateHeader(handle: FileHandle, sampleRate: Double, dataSize: UInt32) {
        handle.seek(toFileOffset: 0)
        handle.write(wavHeader(sampleRate: sampleRate, dataSize: dataSize))
        handle.seekToEndOfFile()
    }

    private func wavHeader(sampleRate: Double, dataSize: UInt32) -> Data {
        var data = Data()
        func text(_ value: String) { data.append(Data(value.utf8)) }
        func u16(_ value: UInt16) {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
        }
        func u32(_ value: UInt32) {
            var little = value.littleEndian
            withUnsafeBytes(of: &little) { data.append(contentsOf: $0) }
        }
        let rate = UInt32(sampleRate.rounded())
        text("RIFF")
        u32(36 + dataSize)
        text("WAVEfmt ")
        u32(16)
        u16(1)
        u16(1)
        u32(rate)
        u32(rate * 2)
        u16(2)
        u16(16)
        text("data")
        u32(dataSize)
        return data
    }

    private static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd-HHmmss"
        return formatter
    }()
}

final class CaptionWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

final class CaptionTextView: NSTextView {
    override func keyDown(with event: NSEvent) {
        if event.modifierFlags.contains(.command),
           event.charactersIgnoringModifiers?.lowercased() == "c" {
            copyCaptionText()
            return
        }
        super.keyDown(with: event)
    }

    private func copyCaptionText() {
        let value = selectedRange().length > 0
            ? (string as NSString).substring(with: selectedRange())
            : string
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }
}

final class DragHandleView: NSView {
    private var dragStartScreen: NSPoint?
    private var windowStartOrigin: NSPoint?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = NSColor(calibratedWhite: 0.35, alpha: 0.95).cgColor
        layer?.cornerRadius = 5
        toolTip = "Drag to move"
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        let dot = NSColor(calibratedWhite: 0.75, alpha: 1)
        let size: CGFloat = 3
        let cx = bounds.midX - size / 2
        let top = NSRect(x: cx, y: bounds.midY + 2, width: size, height: size)
        let bottom = NSRect(x: cx, y: bounds.midY - 5, width: size, height: size)
        dot.setFill()
        NSBezierPath(ovalIn: top).fill()
        NSBezierPath(ovalIn: bottom).fill()
    }

    override func resetCursorRects() {
        addCursorRect(bounds, cursor: .openHand)
    }

    override func mouseDown(with event: NSEvent) {
        NSCursor.closedHand.push()
        dragStartScreen = NSEvent.mouseLocation
        windowStartOrigin = window?.frame.origin
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window,
              let dragStartScreen,
              let windowStartOrigin else { return }
        let current = NSEvent.mouseLocation
        var origin = NSPoint(
            x: windowStartOrigin.x + (current.x - dragStartScreen.x),
            y: windowStartOrigin.y + (current.y - dragStartScreen.y)
        )
        origin = Self.clampedOrigin(origin, size: window.frame.size)
        window.setFrameOrigin(origin)
    }

    override func mouseUp(with event: NSEvent) {
        NSCursor.pop()
        dragStartScreen = nil
        windowStartOrigin = nil
    }

    static func clampedOrigin(_ origin: NSPoint, size: NSSize) -> NSPoint {
        let screenFrame = NSScreen.screens
            .map(\.visibleFrame)
            .first(where: { $0.contains(NSPoint(x: origin.x + size.width / 2, y: origin.y + size.height / 2)) })
            ?? NSScreen.main?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        let minVisible: CGFloat = 24
        let minX = screenFrame.minX + minVisible - size.width
        let maxX = screenFrame.maxX - minVisible
        let minY = screenFrame.minY
        let maxY = screenFrame.maxY - minVisible
        return NSPoint(
            x: min(max(origin.x, minX), maxX),
            y: min(max(origin.y, minY), maxY)
        )
    }
}

final class SubtitleWindow {
    private static let controlBarHeight: CGFloat = 34
    private static let controlButtonWidth: CGFloat = 52
    private static let controlButtonHeight: CGFloat = 22
    private static let modelButtonWidth: CGFloat = 108
    private static let dragHandleSize: CGFloat = 22
    private static let controlGap: CGFloat = 6
    private static let controlPadding: CGFloat = 6
    private static let collapsedWidth: CGFloat =
        controlPadding + dragHandleSize + controlGap
        + modelButtonWidth + controlGap
        + controlButtonWidth + controlGap
        + controlButtonWidth + controlPadding

    private let window: NSWindow
    private let stopScript: String
    /// The pane this window shows. One caption region, fixed for the whole run.
    private let pane: Source
    private let textView = CaptionTextView()
    private let scrollView = NSScrollView()
    private let dragHandle = DragHandleView(frame: .zero)
    private let modelButton = NSButton(title: "", target: nil, action: nil)
    private let hideButton = NSButton(title: "Hide", target: nil, action: nil)
    private let quitButton = NSButton(title: "Quit", target: nil, action: nil)
    private var choices: [ASRChoice] = []
    private var currentChoice: ASRChoice?
    /// Called on the main thread when the user picks another entry in the model dropdown.
    var onSelectChoice: ((ASRChoice) -> Void)?
    private var stopProcess: Process?
    private var expandedSize: NSSize
    private var collapsed = false
    private var lineStart = 0
    private var debugPrefix = ""
    private var liveText: String?
    private var liveColor = NSColor.white
    /// Channel the pane is currently showing; only the auto pane ever has one.
    private var speaker: Source?

    init(config: Config) {
        stopScript = config.stopScript
        pane = config.pane
        let screen = NSScreen.main?.frame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        let rect = NSRect(x: screen.minX, y: screen.minY, width: screen.width, height: config.height)
        expandedSize = rect.size
        window = CaptionWindow(
            contentRect: rect,
            styleMask: .borderless,
            backing: .buffered,
            defer: false
        )
        window.level = .floating
        window.isOpaque = false
        window.backgroundColor = NSColor.black.withAlphaComponent(config.opacity)
        window.ignoresMouseEvents = false
        window.hasShadow = false
        window.collectionBehavior = [.canJoinAllSpaces, .stationary]
        window.contentView?.wantsLayer = true
        window.contentView?.layer?.cornerRadius = 8
        window.contentView?.layer?.masksToBounds = true

        if let content = window.contentView {
            addControls(to: content)
            addRegion(to: content)
            relayoutTextRegions()
        }
        setChoices(config.choices, current: config.currentChoice)
        window.makeKeyAndOrderFront(nil)
    }

    func setChoices(_ choices: [ASRChoice], current: ASRChoice) {
        self.choices = choices
        setCurrentChoice(current)
    }

    func setCurrentChoice(_ choice: ASRChoice) {
        currentChoice = choice
        setButtonTitle(modelButton, choice.buttonTitle)
        modelButton.toolTip = "ASR model: \(choice.menuTitle)"
    }

    func toggleVisibility() {
        collapsed.toggle()
        setButtonTitle(hideButton, collapsed ? "Show" : "Hide")
        if collapsed {
            expandedSize = window.frame.size
            let pillOrigin = NSPoint(
                x: window.frame.maxX - Self.collapsedWidth,
                y: window.frame.minY
            )
            let origin = DragHandleView.clampedOrigin(
                pillOrigin,
                size: NSSize(width: Self.collapsedWidth, height: Self.controlBarHeight)
            )
            window.setFrame(
                NSRect(origin: origin, size: NSSize(width: Self.collapsedWidth, height: Self.controlBarHeight)),
                display: true,
                animate: false
            )
        } else {
            let current = window.frame
            // Keep the control strip under the handle: expand left and up from the pill.
            var origin = NSPoint(
                x: current.maxX - expandedSize.width,
                y: current.minY
            )
            origin = DragHandleView.clampedOrigin(origin, size: expandedSize)
            window.setFrame(NSRect(origin: origin, size: expandedSize), display: true, animate: false)
        }
        relayoutTextRegions()
        if let content = window.contentView {
            layoutControls(in: content.bounds)
        }
    }

    /// A status belongs to the pane rather than to any one channel, so it stays unprefixed on auto.
    func setStatus(_ text: String, color: NSColor = .systemYellow) {
        replaceLine("\(sourceLabel(pane))\(text)", color: color)
        lineStart = textView.textStorage?.length ?? 0
    }

    func update(_ text: String, final: Bool, speaker: Source? = nil) {
        if let speaker { self.speaker = speaker }  // --source auto: one pane, two channels
        liveText = text
        liveColor = final ? NSColor.white : NSColor(white: 0.72, alpha: 1)
        replaceLine("\(linePrefix())\(text)\(final ? "\n" : "")", color: liveColor)
        if final {
            lineStart = textView.textStorage?.length ?? 0
            liveText = nil
        }
    }

    /// Point the auto pane at the channel the gate is on, so the level meter names it even during
    /// a silence that has produced no caption yet.
    func setAutoSpeaker(_ speaker: Source) {
        self.speaker = speaker
    }

    func showDebug(level: Float?) {
        debugPrefix = "\(formatLevel(level))  "
        if let liveText {
            replaceLine("\(linePrefix())\(liveText)", color: liveColor)
        } else {
            replaceLine(linePrefix(), color: NSColor.systemGreen)
        }
    }

    /// Who a caption came from, plus the level meter when --debug is on. On the auto pane the name
    /// is the gated channel's, not the pane's.
    private func linePrefix() -> String {
        "\(sourceLabel(speaker ?? pane))\(debugPrefix)"
    }

    private func formatLevel(_ level: Float?) -> String {
        guard let level else { return "waiting" }
        return String(format: "%.1f dB", level)
    }

    private func addRegion(to content: NSView) {
        let frame = textFrame(in: content.bounds)
        scrollView.frame = frame
        scrollView.hasVerticalScroller = false
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        scrollView.autoresizingMask = [.width, .height]
        scrollView.verticalScrollElasticity = .allowed

        textView.frame = NSRect(x: 0, y: 0, width: frame.width, height: frame.height)
        textView.minSize = NSSize(width: 0, height: frame.height)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.isEditable = false
        textView.isSelectable = true
        textView.drawsBackground = false
        textView.textColor = .white
        textView.font = .monospacedSystemFont(ofSize: 14, weight: .regular)
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.heightTracksTextView = false
        textView.textContainer?.containerSize = NSSize(width: frame.width, height: CGFloat.greatestFiniteMagnitude)
        scrollView.documentView = textView
        content.addSubview(scrollView)
    }

    private func addControls(to content: NSView) {
        modelButton.target = self
        modelButton.action = #selector(modelClicked)
        hideButton.target = self
        hideButton.action = #selector(toggleClicked)
        quitButton.target = self
        quitButton.action = #selector(quitClicked)
        styleButton(modelButton, title: "", background: NSColor(calibratedRed: 0.22, green: 0.24, blue: 0.29, alpha: 1))
        styleButton(hideButton, title: "Hide", background: NSColor(calibratedRed: 0.10, green: 0.34, blue: 0.50, alpha: 1))
        styleButton(quitButton, title: "Quit", background: NSColor(calibratedRed: 0.62, green: 0.12, blue: 0.15, alpha: 1))
        dragHandle.autoresizingMask = [.minXMargin, .maxYMargin]
        content.addSubview(dragHandle)
        content.addSubview(modelButton)
        content.addSubview(hideButton)
        content.addSubview(quitButton)
        layoutControls(in: content.bounds)
    }

    private func layoutControls(in bounds: NSRect) {
        let y = bounds.minY + Self.controlPadding
        let quitX = bounds.maxX - Self.controlPadding - Self.controlButtonWidth
        let hideX = quitX - Self.controlGap - Self.controlButtonWidth
        let modelX = hideX - Self.controlGap - Self.modelButtonWidth
        let dragX = modelX - Self.controlGap - Self.dragHandleSize
        quitButton.frame = NSRect(x: quitX, y: y, width: Self.controlButtonWidth, height: Self.controlButtonHeight)
        hideButton.frame = NSRect(x: hideX, y: y, width: Self.controlButtonWidth, height: Self.controlButtonHeight)
        modelButton.frame = NSRect(x: modelX, y: y, width: Self.modelButtonWidth, height: Self.controlButtonHeight)
        dragHandle.frame = NSRect(x: dragX, y: y, width: Self.dragHandleSize, height: Self.dragHandleSize)
    }

    private func textFrame(in bounds: NSRect) -> NSRect {
        NSRect(
            x: 10,
            y: Self.controlBarHeight,
            width: bounds.width - 20,
            height: max(40, bounds.height - Self.controlBarHeight - 5)
        )
    }

    private func relayoutTextRegions() {
        guard let content = window.contentView else { return }
        scrollView.isHidden = collapsed
        scrollView.frame = textFrame(in: content.bounds)
    }

    private func styleButton(_ button: NSButton, title: String, background: NSColor) {
        button.isBordered = false
        button.wantsLayer = true
        button.layer?.backgroundColor = background.cgColor
        button.layer?.cornerRadius = 5
        button.autoresizingMask = [.minXMargin, .maxYMargin]
        setButtonTitle(button, title)
    }

    private func setButtonTitle(_ button: NSButton, _ title: String) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        paragraph.lineBreakMode = .byTruncatingTail
        button.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
                .paragraphStyle: paragraph
            ]
        )
    }

    @objc private func toggleClicked() {
        toggleVisibility()
    }

    @objc private func modelClicked() {
        let menu = NSMenu()
        for choice in choices {
            let item = NSMenuItem(title: choice.menuTitle, action: #selector(modelPicked(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = choice.id
            item.state = choice.id == currentChoice?.id ? .on : .off
            menu.addItem(item)
        }
        menu.popUp(positioning: nil, at: NSPoint(x: 0, y: modelButton.bounds.maxY + 4), in: modelButton)
    }

    @objc private func modelPicked(_ sender: NSMenuItem) {
        guard
            let id = sender.representedObject as? String,
            let choice = choices.first(where: { $0.id == id })
        else { return }
        onSelectChoice?(choice)
    }

    @objc private func quitClicked() {
        replaceLine("Stopping LiveCaption... result: logs/subtitle-stop.log\n", color: NSColor.systemOrange)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [stopScript]
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self else { return }
                let status = process.terminationStatus
                self.replaceLine("stop.sh finished with exit \(status). See logs/subtitle-stop.log\n",
                                 color: status == 0 ? NSColor.systemGreen : NSColor.systemRed)
                self.stopProcess = nil
            }
        }
        do {
            stopProcess = process
            try process.run()
        } catch {
            stopProcess = nil
            replaceLine("Could not start stop.sh: \(error.localizedDescription)\n", color: NSColor.systemRed)
            NSApp.terminate(nil)
        }
    }

    private func replaceLine(_ text: String, color: NSColor) {
        guard let storage = textView.textStorage else { return }
        let shouldFollow = isNearBottom()
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 14, weight: .regular),
            .foregroundColor: color
        ]
        let attributed = NSAttributedString(string: text, attributes: attrs)
        storage.replaceCharacters(in: NSRange(location: lineStart, length: storage.length - lineStart), with: attributed)
        if shouldFollow {
            textView.scrollRangeToVisible(NSRange(location: storage.length, length: 0))
        }
    }

    private func isNearBottom() -> Bool {
        let visible = scrollView.contentView.bounds
        let documentHeight = textView.bounds.height
        return documentHeight - visible.maxY < 24
    }
}

final class AppleASR {
    private static let sessionDuration: TimeInterval = 50
    private let source: Source
    private let recognizer: SFSpeechRecognizer
    private let onText: (Source, String, Bool, String) -> Void
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var lastText = ""
    private var sessionID = 0
    private let lock = NSLock()

    init(source: Source, language: String, onText: @escaping (Source, String, Bool, String) -> Void) throws {
        self.source = source
        self.onText = onText
        let locale = Locale(identifier: language)
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable else {
            throw NSError(domain: "LiveCaption", code: 1, userInfo: [NSLocalizedDescriptionKey: "Apple Speech unavailable for \(language)"])
        }
        self.recognizer = recognizer
        startSession()
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        request?.append(buffer)
        lock.unlock()
    }

    func append(_ sampleBuffer: CMSampleBuffer) {
        lock.lock()
        request?.appendAudioSampleBuffer(sampleBuffer)
        lock.unlock()
    }

    /// End the session for good. Bumping sessionID also disarms the pending rotation timer.
    func stop() {
        lock.lock()
        sessionID += 1
        task?.cancel()
        request?.endAudio()
        task = nil
        request = nil
        lastText = ""
        lock.unlock()
    }

    private func startSession() {
        lock.lock()
        sessionID += 1
        let currentSessionID = sessionID
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        request.requiresOnDeviceRecognition = false
        self.request = request
        self.lastText = ""
        self.task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            self?.handle(sessionID: currentSessionID, result: result, error: error)
        }
        lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.sessionDuration) { [weak self] in
            self?.restart(sessionID: currentSessionID, commitPartial: true)
        }
    }

    private func handle(sessionID: Int, result: SFSpeechRecognitionResult?, error: Error?) {
        lock.lock()
        let isCurrentSession = sessionID == self.sessionID
        lock.unlock()
        guard isCurrentSession else { return }

        if let error {
            let nsError = error as NSError
            fputs("Apple Speech error (\(source.rawValue), \(nsError.domain) \(nsError.code)): \(error.localizedDescription)\n", stderr)
            restart(sessionID: sessionID, commitPartial: true, delay: 0.5)
            return
        }
        guard let result else { return }
        let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
        lock.lock()
        guard sessionID == self.sessionID else {
            lock.unlock()
            return
        }
        let shouldEmit = !text.isEmpty && (result.isFinal || text != lastText)
        if shouldEmit {
            lastText = text
        }
        lock.unlock()
        if shouldEmit {
            onText(source, text, result.isFinal, "")
        }
        if result.isFinal {
            restart(sessionID: sessionID, commitPartial: false)
        }
    }

    private func restart(sessionID: Int, commitPartial: Bool, delay: TimeInterval = 0) {
        lock.lock()
        guard sessionID == self.sessionID else {
            lock.unlock()
            return
        }
        self.sessionID += 1
        let partial = commitPartial ? lastText : ""
        task?.cancel()
        request?.endAudio()
        task = nil
        request = nil
        lock.unlock()
        if !partial.isEmpty {
            onText(source, partial, true, "")
        }
        fputs("Apple Speech session rotated (\(source.rawValue))\n", stderr)
        if delay > 0 {
            DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
                self?.startSession()
            }
        } else {
            startSession()
        }
    }
}

final class SubprocessASR {
    // ponytail: a busy worker stops reading stdin during inference, so writes must not run on the
    // audio thread -- they queue here and are dropped once the worker is a few seconds behind.
    private static let maxQueuedBytes = 4 * 1024 * 1024

    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let onText: (Source, String, Bool, String) -> Void
    private let onReady: (String) -> Void
    private let onExit: (Int32) -> Void
    private let writeQueue = DispatchQueue(label: "LiveCaption.asr-stdin")
    private let lock = NSLock()
    private var stdoutBuffer = ""
    private var queuedBytes = 0
    private var closed = false

    convenience init(
        script: String,
        arguments: [String] = [],
        python: String = "python3",
        onText: @escaping (Source, String, Bool, String) -> Void,
        onReady: @escaping (String) -> Void,
        onExit: @escaping (Int32) -> Void
    ) throws {
        try self.init(command: ["/usr/bin/env", python, script] + arguments, onText: onText, onReady: onReady, onExit: onExit)
    }

    init(
        command: [String],
        onText: @escaping (Source, String, Bool, String) -> Void,
        onReady: @escaping (String) -> Void,
        onExit: @escaping (Int32) -> Void
    ) throws {
        self.onText = onText
        self.onReady = onReady
        self.onExit = onExit
        process.executableURL = URL(fileURLWithPath: command[0])
        process.arguments = Array(command.dropFirst())
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.standardError

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.consume(handle.availableData)
        }
        process.terminationHandler = { [weak self] process in
            guard let self else { return }
            self.lock.lock()
            let expected = self.closed
            self.lock.unlock()
            // a shutdown() we asked for is not worth reporting; a crash or a missing dep is
            guard !expected else { return }
            self.onExit(process.terminationStatus)
        }
        try process.run()
    }

    deinit {
        shutdown()
    }

    /// Stop feeding and kill the worker. Safe to call while audio threads are still sending.
    func shutdown() {
        lock.lock()
        let alreadyClosed = closed
        closed = true
        lock.unlock()
        guard !alreadyClosed else { return }
        output.fileHandleForReading.readabilityHandler = nil
        try? input.fileHandleForWriting.close()
        if process.isRunning {
            process.terminate()
        }
    }

    func send(source: Source, sampleRate: Double, floats: [Float]) {
        guard !floats.isEmpty else { return }
        let data = floats.withUnsafeBufferPointer { Data(buffer: $0) }
        let payload: [String: Any] = [
            "type": "audio",
            "source": source.rawValue,
            "sampleRate": Int(sampleRate),
            "channels": 1,
            "pcmFloat32": data.base64EncodedString()
        ]
        guard
            let json = try? JSONSerialization.data(withJSONObject: payload),
            var line = String(data: json, encoding: .utf8)
        else { return }
        line.append("\n")
        let bytes = Data(line.utf8)

        lock.lock()
        guard !closed, queuedBytes + bytes.count <= Self.maxQueuedBytes else {
            lock.unlock()
            return
        }
        queuedBytes += bytes.count
        lock.unlock()

        writeQueue.async { [weak self] in
            guard let self else { return }
            self.lock.lock()
            let stopped = self.closed
            self.queuedBytes -= bytes.count
            self.lock.unlock()
            guard !stopped else { return }
            try? self.input.fileHandleForWriting.write(contentsOf: bytes)
        }
    }

    private func consume(_ data: Data) {
        guard !data.isEmpty else {
            output.fileHandleForReading.readabilityHandler = nil
            return
        }
        guard let text = String(data: data, encoding: .utf8) else { return }
        stdoutBuffer.append(text)
        let parts = stdoutBuffer.split(separator: "\n", omittingEmptySubsequences: false)
        stdoutBuffer = parts.last.map(String.init) ?? ""
        for line in parts.dropLast() {
            if line.isEmpty { continue }
            guard
                let jsonData = line.data(using: .utf8),
                let obj = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
            else { continue }
            if obj["status"] as? String == "ready" {
                onReady(obj["device"] as? String ?? "")
                continue
            }
            guard
                let sourceRaw = obj["source"] as? String,
                let source = Source(rawValue: sourceRaw),
                let transcript = obj["text"] as? String
            else { continue }
            let final = obj["final"] as? Bool ?? true
            onText(source, transcript, final, obj["language"] as? String ?? "")
        }
    }
}

final class MicCapture {
    private let engine = AVAudioEngine()
    private let source: Source = .mic
    private let onPCM: (Source, AVAudioPCMBuffer) -> Void
    private let onFloats: ((Source, Double, [Float]) -> Void)?

    init(onPCM: @escaping (Source, AVAudioPCMBuffer) -> Void, onFloats: ((Source, Double, [Float]) -> Void)?) {
        self.onPCM = onPCM
        self.onFloats = onFloats
    }

    func start() throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 8192, format: format) { [weak self] buffer, _ in
            guard let self else { return }
            self.onPCM(self.source, buffer)
            if let onFloats = self.onFloats, let floats = floats(from: buffer) {
                onFloats(self.source, buffer.format.sampleRate, floats)
            }
        }
        engine.prepare()
        try engine.start()
    }
}

final class SystemCapture: NSObject, SCStreamOutput {
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "LiveCaption.system-audio")
    private let onSampleBuffer: (Source, CMSampleBuffer) -> Void
    private let onFloats: ((Source, Double, [Float]) -> Void)?

    init(onSampleBuffer: @escaping (Source, CMSampleBuffer) -> Void, onFloats: ((Source, Double, [Float]) -> Void)?) {
        self.onSampleBuffer = onSampleBuffer
        self.onFloats = onFloats
    }

    func start() {
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { [weak self] content, error in
            guard let self else { return }
            if let error {
                fputs("ScreenCaptureKit error: \(error.localizedDescription)\n", stderr)
                return
            }
            guard let display = content?.displays.first else {
                fputs("No display available for system audio capture\n", stderr)
                return
            }
            let filter = SCContentFilter(display: display, excludingWindows: [])
            let config = SCStreamConfiguration()
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            config.queueDepth = 1
            config.capturesAudio = true
            config.sampleRate = 16_000
            config.channelCount = 1
            config.excludesCurrentProcessAudio = true

            let stream = SCStream(filter: filter, configuration: config, delegate: nil)
            do {
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: self.queue)
            } catch {
                fputs("Could not add system audio output: \(error.localizedDescription)\n", stderr)
                return
            }
            stream.startCapture { error in
                if let error {
                    fputs("Could not start system audio capture: \(error.localizedDescription)\n", stderr)
                }
            }
            self.stream = stream
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio else { return }
        onSampleBuffer(.sys, sampleBuffer)
        if let onFloats, let (rate, floats) = floats(from: sampleBuffer) {
            onFloats(.sys, rate, floats)
        }
    }
}

/// Keeps whichever channel is talking and drops the other one, resampled to 16 kHz.
///
/// The speaker wins while it has voice and the microphone gets the rest: in a meeting the far end
/// is the side you cannot ask to repeat itself, and your own voice is the one you already heard.
/// The hangover stops a pause inside a sentence from handing the channel back and forth mid-word.
/// Only `--source auto` uses it, on every backend; the caption it produces carries the name of
/// whichever channel won.
final class AudioGate {
    private let targetRate = 16_000.0
    private let systemVoiceThreshold: Float = -45
    private let systemHangover: TimeInterval = 0.6
    private let lock = NSLock()
    private var lastSystemVoice = Date.distantPast
    private var resamplePositions: [Source: Double] = [:]
    private var current: Source = .mic

    /// The channel the last processed frame belonged to; drives the caption prefix under auto.
    var selected: Source {
        lock.lock()
        defer { lock.unlock() }
        return current
    }

    func process(source: Source, sampleRate: Double, floats: [Float]) -> [Float]? {
        guard !floats.isEmpty, sampleRate > 0 else { return nil }
        lock.lock()
        defer { lock.unlock() }

        let now = Date()
        if source == .sys && rmsDB(floats) >= systemVoiceThreshold {
            lastSystemVoice = now
        }
        current = now.timeIntervalSince(lastSystemVoice) <= systemHangover ? .sys : .mic
        guard source == current else { return nil }
        return resample(source: source, sampleRate: sampleRate, floats: floats)
    }

    private func resample(source: Source, sampleRate: Double, floats: [Float]) -> [Float] {
        guard abs(sampleRate - targetRate) > 1 else { return floats }
        let step = sampleRate / targetRate
        var position = resamplePositions[source] ?? 0
        var output: [Float] = []
        output.reserveCapacity(Int(Double(floats.count) / step) + 1)
        while position < Double(floats.count) {
            let lower = Int(position)
            let upper = min(lower + 1, floats.count - 1)
            let fraction = Float(position - Double(lower))
            output.append(floats[lower] + (floats[upper] - floats[lower]) * fraction)
            position += step
        }
        resamplePositions[source] = position - Double(floats.count)
        return output
    }
}

final class AppController: NSObject, NSApplicationDelegate {
    private var config: Config
    // config is mutated on the main thread when the model changes; audio threads read these copies
    private let sourceMode: SourceMode
    private let debugEnabled: Bool
    private let writer: TranscriptWriter
    private let debugRecorder: DebugRecorder?
    private var subtitle: SubtitleWindow?
    // audio threads read these while the main thread swaps models, so both go through asrLock
    private let asrLock = NSLock()
    private var appleASR: AppleASR?
    private var pythonASR: SubprocessASR?
    private var asrGeneration = 0
    private let audioGate = AudioGate()
    private var micCapture: MicCapture?
    private var systemCapture: SystemCapture?
    private var debugLevel: Float?
    /// The unfinished line, flushed to the transcript on quit.
    private var pendingText: (text: String, speaker: Source?, language: String)?
    /// Channel the auto pane's unfinished line was credited to; main thread only.
    private var utteranceSource: Source?
    private var lastDebugDraw = Date.distantPast

    init(config: Config) {
        self.config = config
        self.sourceMode = config.sourceMode
        self.debugEnabled = config.debug
        self.writer = TranscriptWriter(path: config.outputDir)
        // ponytail: --record reuses the debug WAV recorder, just without the level overlay
        self.debugRecorder = config.debug
            ? DebugRecorder(path: config.debugDir)
            : (config.record ? DebugRecorder(path: config.recordDir) : nil)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        subtitle = SubtitleWindow(config: config)
        subtitle?.onSelectChoice = { [weak self] choice in self?.switchTo(choice) }
        if config.debug {
            subtitle?.showDebug(level: debugLevel)
        } else {
            subtitle?.setStatus("Listening...\n")
        }
        NSApp.activate(ignoringOtherApps: true)
        requestPermissionsThenStart()
    }

    private func start() {
        do {
            try setupASR()
            try startAudio()
        } catch {
            fputs("\(error.localizedDescription)\n", stderr)
            showPermissionAlert(
                title: "LiveCaption could not start",
                message: error.localizedDescription,
                settingsURL: nil
            )
        }
    }

    private func requestPermissionsThenStart() {
        requestSpeechPermission(needed: config.asrMode == .apple) { [weak self] speechOK in
            guard let self else { return }
            guard speechOK else {
                self.showPermissionAlert(
                    title: "Speech Recognition permission needed",
                    message: "Enable Speech Recognition for live-subtitle, then start LiveCaption again.",
                    settingsURL: "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition"
                )
                return
            }
            self.requestMicrophonePermissionIfNeeded { micOK in
                guard micOK else {
                    self.showPermissionAlert(
                        title: "Microphone permission needed",
                        message: "Enable Microphone access for live-subtitle, then start LiveCaption again.",
                        settingsURL: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
                    )
                    return
                }
                self.start()
            }
        }
    }

    private func requestSpeechPermission(needed: Bool, _ completion: @escaping (Bool) -> Void) {
        guard needed else {
            completion(true)
            return
        }
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized:
            completion(true)
        case .notDetermined:
            SFSpeechRecognizer.requestAuthorization { status in
                DispatchQueue.main.async { completion(status == .authorized) }
            }
        case .denied, .restricted:
            completion(false)
        @unknown default:
            completion(false)
        }
    }

    private func requestMicrophonePermissionIfNeeded(_ completion: @escaping (Bool) -> Void) {
        guard config.sourceMode.sources.contains(.mic) else {
            completion(true)
            return
        }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            completion(true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async { completion(granted) }
            }
        case .denied, .restricted:
            completion(false)
        @unknown default:
            completion(false)
        }
    }

    private func showPermissionAlert(title: String, message: String, settingsURL: String?) {
        DispatchQueue.main.async {
            self.subtitle?.setStatus("\(title)\n")
            NSApp.activate(ignoringOtherApps: true)
            let alert = NSAlert()
            alert.messageText = title
            alert.informativeText = message
            if settingsURL != nil {
                alert.addButton(withTitle: "Open Settings")
            }
            alert.addButton(withTitle: "Quit")
            let response = alert.runModal()
            if response == .alertFirstButtonReturn, let settingsURL, let url = URL(string: settingsURL) {
                NSWorkspace.shared.open(url)
            }
            NSApp.terminate(nil)
        }
    }

    private func setupASR() throws {
        let generation = asrGeneration
        let choice = config.currentChoice
        switch config.asrMode {
        case .apple:
            // one pane, so one realtime task -- under --source auto the gate has already picked
            // which channel reaches it
            let built = try AppleASR(source: config.pane, language: localeID(config.language),
                                     onText: handleText)
            asrLock.lock()
            appleASR = built
            asrLock.unlock()
        case .hf, .hfStream, .sherpa:
            let onReady: (String) -> Void = { [weak self] device in
                self?.onMain(generation) {
                    $0.report("\(choice.menuTitle) ready\(device.isEmpty ? "" : " (\(device))")", color: .systemGreen)
                }
            }
            let onExit: (Int32) -> Void = { [weak self] status in
                self?.onMain(generation) {
                    $0.asrLock.lock()
                    $0.pythonASR = nil
                    $0.asrLock.unlock()
                    $0.report("\(choice.menuTitle) stopped (exit \(status)) — pick another model", color: .systemRed)
                }
            }
            let worker: SubprocessASR
            switch config.asrMode {
            case .hf:
                worker = try SubprocessASR(script: config.hfScript,
                                           arguments: ["--hf-model", config.hfModel ?? ""],
                                           onText: handleText, onReady: onReady, onExit: onExit)
            case .hfStream:
                worker = try SubprocessASR(script: config.hfStreamScript,
                                           arguments: ["--hf-model", config.hfModel ?? "",
                                                       "--language", config.language],
                                           python: config.hfStreamPython,
                                           onText: handleText, onReady: onReady, onExit: onExit)
            default:
                worker = try SubprocessASR(script: config.sherpaScript,
                                           onText: handleText, onReady: onReady, onExit: onExit)
            }
            asrLock.lock()
            pythonASR = worker
            asrLock.unlock()
        }
    }

    /// Run `body` on the main thread, but only while `generation` is still the live model.
    private func onMain(_ generation: Int, _ body: @escaping (AppController) -> Void) {
        DispatchQueue.main.async { [weak self] in
            guard let self, generation == self.asrGeneration else { return }
            body(self)
        }
    }

    private func stopASR() {
        utteranceSource = nil  // the next model opens its own lines; do not credit them to the old
        asrLock.lock()
        asrGeneration += 1
        let apple = appleASR
        let python = pythonASR
        appleASR = nil
        pythonASR = nil
        asrLock.unlock()
        apple?.stop()
        python?.shutdown()
    }

    private func switchTo(_ choice: ASRChoice) {
        guard choice != config.currentChoice else { return }
        // Apple Speech may never have been authorised if the run started on a Python worker
        requestSpeechPermission(needed: choice.mode == .apple) { [weak self] speechOK in
            guard let self else { return }
            guard speechOK else {
                self.subtitle?.setCurrentChoice(self.config.currentChoice)
                self.report("Speech Recognition permission denied — staying on \(self.config.currentChoice.menuTitle)",
                            color: .systemRed)
                return
            }
            self.applyChoice(choice)
        }
    }

    private func applyChoice(_ choice: ASRChoice) {
        stopASR()
        config.asrMode = choice.mode
        if choice.isHF {
            config.hfModel = choice.hfModel
        }
        subtitle?.setCurrentChoice(choice)
        report("Switching to \(choice.menuTitle)...", color: .systemYellow)
        do {
            try setupASR()
            if choice.mode == .apple {
                report("\(choice.menuTitle) ready", color: .systemGreen)
            }
        } catch {
            report("Could not start \(choice.menuTitle): \(error.localizedDescription)", color: .systemRed)
        }
    }

    /// Status line in the caption pane -- a failed model switch must not kill the app the way
    /// showPermissionAlert() does, the user still has the dropdown to pick something that works.
    private func report(_ text: String, color: NSColor) {
        subtitle?.setStatus("\(text)\n", color: color)
    }

    private func startAudio() throws {
        // ponytail: the model can change mid-run, so both callbacks stay installed the whole time
        let onFloats: (Source, Double, [Float]) -> Void = { [weak self] source, rate, floats in
            self?.handleFloats(source: source, sampleRate: rate, floats: floats)
        }

        // ponytail: single-channel modes hand Apple Speech the capture buffer untouched; under
        // --source auto everything goes through handleFloats instead, so the gate can pick first.
        let onPCM: (Source, AVAudioPCMBuffer) -> Void = { [weak self] _, buffer in
            guard let self, self.sourceMode != .auto else { return }
            self.apple()?.append(buffer)
        }
        let onSampleBuffer: (Source, CMSampleBuffer) -> Void = { [weak self] _, buffer in
            guard let self, self.sourceMode != .auto else { return }
            self.apple()?.append(buffer)
        }

        if config.sourceMode.sources.contains(.mic) {
            micCapture = MicCapture(onPCM: onPCM, onFloats: onFloats)
            try micCapture?.start()
        }
        if config.sourceMode.sources.contains(.sys) {
            systemCapture = SystemCapture(onSampleBuffer: onSampleBuffer, onFloats: onFloats)
            systemCapture?.start()
        }
    }

    private func apple() -> AppleASR? {
        asrLock.lock()
        defer { asrLock.unlock() }
        return appleASR
    }

    private func handleFloats(source: Source, sampleRate: Double, floats: [Float]) {
        asrLock.lock()
        let python = pythonASR
        let apple = appleASR
        asrLock.unlock()

        let gating = sourceMode == .auto
        var gatedLevel: Float?
        if gating {
            // ponytail: the winning channel reaches the recognizer under one .auto label so the
            // stream never breaks. Forwarding it as mic/sys instead would starve whichever side is
            // quiet, and a streaming recognizer needs that silence to finish its sentence.
            if let gated = audioGate.process(source: source, sampleRate: sampleRate, floats: floats) {
                python?.send(source: .auto, sampleRate: 16_000, floats: gated)
                if python == nil, let apple, let buffer = pcmBuffer(sampleRate: 16_000, floats: gated) {
                    apple.append(buffer)
                }
                gatedLevel = rmsDB(gated)
            }
        } else if let python {
            python.send(source: source, sampleRate: sampleRate, floats: floats)
        }

        guard let debugRecorder else { return }
        let level = debugRecorder.record(source: source, sampleRate: sampleRate, floats: floats)
        guard debugEnabled else { return }
        DispatchQueue.main.async {
            if gating {
                guard let gatedLevel else { return }  // the muted channel has no meter of its own
                self.debugLevel = gatedLevel
                self.subtitle?.setAutoSpeaker(self.audioGate.selected)
            } else {
                self.debugLevel = level
            }
            let now = Date()
            guard now.timeIntervalSince(self.lastDebugDraw) >= 0.15 else { return }
            self.lastDebugDraw = now
            self.subtitle?.showDebug(level: self.debugLevel)
        }
    }

    private func handleText(source: Source, text: String, final: Bool, language: String) {
        let heard = source == .auto ? audioGate.selected : source
        DispatchQueue.main.async {
            // ponytail: a line's channel is locked when it opens, not re-read as it grows -- the
            // gate can hand over mid-sentence, and relabelling half a caption reads worse than
            // crediting all of it to whoever started talking.
            var speaker: Source?
            if source == .auto {
                speaker = self.utteranceSource ?? heard
                self.utteranceSource = final ? nil : speaker
            }
            // ponytail: the detected language goes to the transcript only. The captions already
            // read as one language or the other, and the pane has enough prefixes on it.
            self.subtitle?.update(text, final: final, speaker: speaker)
            if final {
                self.writer.append(source: source, text: text, speaker: speaker, language: language)
                self.pendingText = nil
            } else {
                self.pendingText = (text, speaker, language)
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let pendingText {
            writer.append(source: config.pane, text: pendingText.text, speaker: pendingText.speaker,
                          language: pendingText.language)
        }
        writer.close()
        debugRecorder?.close()
    }
}

func floats(from buffer: AVAudioPCMBuffer) -> [Float]? {
    let frames = Int(buffer.frameLength)
    guard frames > 0 else { return nil }
    let channels = Int(buffer.format.channelCount)

    if let channelData = buffer.floatChannelData {
        if channels == 1 {
            return Array(UnsafeBufferPointer(start: channelData[0], count: frames))
        }
        var mixed = [Float](repeating: 0, count: frames)
        for channel in 0..<channels {
            let values = UnsafeBufferPointer(start: channelData[channel], count: frames)
            for frame in 0..<frames {
                mixed[frame] += values[frame] / Float(channels)
            }
        }
        return mixed
    }

    if let channelData = buffer.int16ChannelData {
        var mixed = [Float](repeating: 0, count: frames)
        for channel in 0..<channels {
            let values = UnsafeBufferPointer(start: channelData[channel], count: frames)
            for frame in 0..<frames {
                mixed[frame] += Float(values[frame]) / Float(Int16.max) / Float(channels)
            }
        }
        return mixed
    }

    return nil
}

func floats(from sampleBuffer: CMSampleBuffer) -> (Double, [Float])? {
    guard
        let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
        let asbdPointer = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription)
    else { return nil }

    let asbd = asbdPointer.pointee
    let frames = CMSampleBufferGetNumSamples(sampleBuffer)
    guard frames > 0 else { return nil }

    var list = AudioBufferList()
    var blockBuffer: CMBlockBuffer?
    let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sampleBuffer,
        bufferListSizeNeededOut: nil,
        bufferListOut: &list,
        bufferListSize: MemoryLayout<AudioBufferList>.size,
        blockBufferAllocator: nil,
        blockBufferMemoryAllocator: nil,
        flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment),
        blockBufferOut: &blockBuffer
    )
    guard status == noErr, let data = list.mBuffers.mData else { return nil }

    let channels = max(1, Int(asbd.mChannelsPerFrame))
    let flags = asbd.mFormatFlags
    let count = Int(list.mBuffers.mDataByteSize)
    if flags & kAudioFormatFlagIsFloat != 0 {
        let samples = count / MemoryLayout<Float>.size
        let values = data.bindMemory(to: Float.self, capacity: samples)
        return (asbd.mSampleRate, averageInterleaved(values, frames: frames, channels: channels))
    }
    if flags & kAudioFormatFlagIsSignedInteger != 0 && asbd.mBitsPerChannel == 16 {
        let samples = count / MemoryLayout<Int16>.size
        let values = data.bindMemory(to: Int16.self, capacity: samples)
        var out = [Float](repeating: 0, count: frames)
        for frame in 0..<frames {
            var sum: Float = 0
            for channel in 0..<channels {
                let index = min(frame * channels + channel, samples - 1)
                sum += Float(values[index]) / Float(Int16.max)
            }
            out[frame] = sum / Float(channels)
        }
        return (asbd.mSampleRate, out)
    }
    return nil
}

func averageInterleaved(_ values: UnsafeMutablePointer<Float>, frames: Int, channels: Int) -> [Float] {
    if channels == 1 {
        return Array(UnsafeBufferPointer(start: values, count: frames))
    }
    var out = [Float](repeating: 0, count: frames)
    for frame in 0..<frames {
        var sum: Float = 0
        for channel in 0..<channels {
            sum += values[frame * channels + channel]
        }
        out[frame] = sum / Float(channels)
    }
    return out
}

func rmsDB(_ floats: [Float]) -> Float {
    guard !floats.isEmpty else { return -90 }
    var sum = 0.0
    for sample in floats {
        let value = Double(sample)
        sum += value * value
    }
    let rms = sqrt(sum / Double(floats.count))
    guard rms > 0.000001 else { return -90 }
    return Float(max(-90, min(6, 20 * log10(rms))))
}

func pcmBuffer(sampleRate: Double, floats: [Float]) -> AVAudioPCMBuffer? {
    guard
        !floats.isEmpty,
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false),
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(floats.count))
    else { return nil }
    buffer.frameLength = AVAudioFrameCount(floats.count)
    floats.withUnsafeBufferPointer { source in
        buffer.floatChannelData?[0].update(from: source.baseAddress!, count: floats.count)
    }
    return buffer
}

var terminationSignals: [DispatchSourceSignal] = []
func installTerminationHandlers() {
    // ponytail: swapping models kills the worker while audio threads may still be mid-write --
    // without this the resulting SIGPIPE would take the whole app down
    signal(SIGPIPE, SIG_IGN)
    for sig in [SIGTERM, SIGINT] {
        signal(sig, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
        source.setEventHandler {
            NSApp.terminate(nil)
        }
        source.resume()
        terminationSignals.append(source)
    }
}

let config = parseArgs()
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppController(config: config)
app.delegate = delegate
installTerminationHandlers()
print("LiveCaption subtitles running: source=\(config.sourceMode.rawValue), asr=\(config.asrMode.rawValue), output=\(config.outputDir)")
app.run()
