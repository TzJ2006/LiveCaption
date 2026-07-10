import AppKit
import AVFoundation
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit
import Speech

enum Source: String, CaseIterable {
    case mic
    case sys
}

enum SourceMode: String {
    case mic
    case system
    case both

    var sources: [Source] {
        switch self {
        case .mic: return [.mic]
        case .system: return [.sys]
        case .both: return [.mic, .sys]
        }
    }
}

enum ASRMode: String {
    case apple
    case hf
    case sherpa
}

struct Config {
    var sourceMode: SourceMode = .mic
    var asrMode: ASRMode = .apple
    var language = "zh-CN"
    var outputDir = "transcripts"
    var opacity: CGFloat = 0.75
    var height: CGFloat = 120
    var hfModel: String?
    var hfScript: String
    var sherpaScript: String
    var stopScript: String
    var debug = false
    var debugDir: String
    var speechWorker = false
}

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
        hfScript: scriptDir.appendingPathComponent("hf_asr.py").path,
        sherpaScript: scriptDir.appendingPathComponent("sherpa_asr.py").path,
        stopScript: projectDir.appendingPathComponent("scripts/stop.sh").path,
        debugDir: projectDir.appendingPathComponent("debug-audio").path
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
            guard let mode = SourceMode(rawValue: value()) else {
                fputs("Use --source mic|system|both\n", stderr)
                exit(2)
            }
            config.sourceMode = mode
        case "--asr":
            guard let mode = ASRMode(rawValue: value()) else {
                fputs("Use --asr apple|hf|sherpa\n", stderr)
                exit(2)
            }
            config.asrMode = mode
        case "--language":
            config.language = localeID(value())
        case "--output-dir":
            config.outputDir = value()
        case "--opacity":
            config.opacity = CGFloat(Double(value()) ?? 0.75)
        case "--height":
            config.height = CGFloat(Double(value()) ?? 120)
        case "--hf-model":
            config.hfModel = value()
        case "--hf-script":
            config.hfScript = value()
        case "--sherpa-script":
            config.sherpaScript = value()
        case "--debug":
            config.debug = true
        case "--speech-worker":
            config.speechWorker = true
        case "--help", "-h":
            print("""
            Usage:
              live-subtitle --source mic|system|both
              live-subtitle --asr apple --language zh-CN
              live-subtitle --asr hf --hf-model openai/whisper-small
              live-subtitle --asr sherpa
              live-subtitle --debug
            """)
            exit(0)
        default:
            fputs("Unknown option: \(arg)\n", stderr)
            exit(2)
        }
    }

    config.opacity = min(1, max(0.1, config.opacity))
    config.height = min(500, max(70, config.height))
    config.language = localeID(config.language)

    if config.asrMode == .hf && (config.hfModel ?? "").isEmpty {
        fputs("--asr hf requires --hf-model <huggingface/model-id>\n", stderr)
        exit(2)
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

    func append(source: Source, text: String) {
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
        if let data = "[\(time)] \(text)\n".data(using: .utf8) {
            handles[source]?.write(data)
        }
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
    private let queue = DispatchQueue(label: "meetinghelper.debug-audio")
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

final class SubtitleWindow {
    private let window: NSWindow
    private let stopScript: String
    private let sources: [Source]
    private var textViews: [Source: CaptionTextView] = [:]
    private var scrollViews: [Source: NSScrollView] = [:]
    private let hideButton = NSButton(title: "Hide", target: nil, action: nil)
    private let quitButton = NSButton(title: "Quit", target: nil, action: nil)
    private var stopProcess: Process?
    private var expandedFrame: NSRect
    private var collapsed = false
    private var lineStarts: [Source: Int] = [:]
    private var debugPrefixes: [Source: String] = [:]
    private var liveTexts: [Source: String] = [:]
    private var liveColors: [Source: NSColor] = [:]

    init(config: Config) {
        stopScript = config.stopScript
        sources = config.sourceMode == .both ? [.sys, .mic] : config.sourceMode.sources
        let screen = NSScreen.main?.frame ?? NSRect(x: 0, y: 0, width: 1200, height: 800)
        let rect = NSRect(x: screen.minX + 50, y: screen.minY, width: screen.width - 100, height: config.height)
        expandedFrame = rect
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
            let frame = textFrame(in: content.bounds)
            if sources.count == 2 {
                let gap: CGFloat = 8
                let width = (frame.width - gap) / 2
                addRegion(source: .sys, frame: NSRect(x: frame.minX, y: frame.minY, width: width, height: frame.height), to: content)
                addRegion(source: .mic, frame: NSRect(x: frame.minX + width + gap, y: frame.minY, width: width, height: frame.height), to: content)
            } else if let source = sources.first {
                addRegion(source: source, frame: frame, to: content)
            }
        }
        window.makeKeyAndOrderFront(nil)
    }

    func toggleVisibility() {
        collapsed.toggle()
        setButtonTitle(hideButton, collapsed ? "Show" : "Hide")
        scrollViews.values.forEach { $0.isHidden = collapsed }
        if collapsed {
            var frame = window.frame
            frame.size.height = 34
            frame.origin.y = expandedFrame.minY
            window.setFrame(frame, display: true, animate: false)
        } else {
            window.setFrame(expandedFrame, display: true, animate: false)
        }
    }

    func setStatus(_ text: String, source: Source) {
        replaceLine("\(prefix(source))\(text)", color: NSColor.systemYellow, source: source)
        lineStarts[source] = textViews[source]?.textStorage?.length ?? 0
    }

    func update(_ text: String, source: Source, final: Bool) {
        liveTexts[source] = text
        let color = final ? NSColor.white : NSColor(white: 0.72, alpha: 1)
        liveColors[source] = color
        replaceLine("\(debugPrefixes[source] ?? prefix(source))\(text)\(final ? "\n" : "")", color: color, source: source)
        if final {
            lineStarts[source] = textViews[source]?.textStorage?.length ?? 0
            liveTexts[source] = nil
            liveColors[source] = nil
        }
    }

    func showDebug(levels: [Source: Float], sources: [Source]) {
        for source in sources {
            let debugPrefix = "\(prefix(source))\(formatLevel(levels[source]))  "
            debugPrefixes[source] = debugPrefix
            if let liveText = liveTexts[source] {
                replaceLine("\(debugPrefix)\(liveText)", color: liveColors[source] ?? .white, source: source)
            } else {
                replaceLine(debugPrefix, color: NSColor.systemGreen, source: source)
            }
        }
    }

    private func prefix(_ source: Source) -> String {
        source == .mic ? "(microphone) " : "(speaker) "
    }

    private func formatLevel(_ level: Float?) -> String {
        guard let level else { return "waiting" }
        return String(format: "%.1f dB", level)
    }

    private func addRegion(source: Source, frame: NSRect, to content: NSView) {
        let scrollView = NSScrollView()
        let textView = CaptionTextView()
        scrollView.frame = frame
        scrollView.hasVerticalScroller = false
        scrollView.hasHorizontalScroller = false
        scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        scrollView.autoresizingMask = sources.count == 1 ? [.width, .height] : [.height]
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
        scrollViews[source] = scrollView
        textViews[source] = textView
        lineStarts[source] = 0
    }

    private func addControls(to content: NSView) {
        hideButton.target = self
        hideButton.action = #selector(toggleClicked)
        quitButton.target = self
        quitButton.action = #selector(quitClicked)
        styleButton(hideButton, title: "Hide", background: NSColor(calibratedRed: 0.10, green: 0.34, blue: 0.50, alpha: 1))
        styleButton(quitButton, title: "Quit", background: NSColor(calibratedRed: 0.62, green: 0.12, blue: 0.15, alpha: 1))
        content.addSubview(hideButton)
        content.addSubview(quitButton)
        layoutControls(in: content.bounds)
    }

    private func layoutControls(in bounds: NSRect) {
        let y = bounds.minY + 6
        quitButton.frame = NSRect(x: bounds.maxX - 62, y: y, width: 52, height: 22)
        hideButton.frame = NSRect(x: bounds.maxX - 120, y: y, width: 52, height: 22)
    }

    private func textFrame(in bounds: NSRect) -> NSRect {
        NSRect(x: 10, y: 34, width: bounds.width - 20, height: max(40, bounds.height - 39))
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
        button.attributedTitle = NSAttributedString(
            string: title,
            attributes: [
                .foregroundColor: NSColor.white,
                .font: NSFont.systemFont(ofSize: 11, weight: .semibold)
            ]
        )
    }

    @objc private func toggleClicked() {
        toggleVisibility()
    }

    @objc private func quitClicked() {
        sources.forEach {
            replaceLine("Stopping MeetingHelper... result: logs/subtitle-stop.log\n", color: NSColor.systemOrange, source: $0)
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [stopScript]
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self else { return }
                let status = process.terminationStatus
                self.sources.forEach {
                    self.replaceLine("stop.sh finished with exit \(status). See logs/subtitle-stop.log\n", color: status == 0 ? NSColor.systemGreen : NSColor.systemRed, source: $0)
                }
                self.stopProcess = nil
            }
        }
        do {
            stopProcess = process
            try process.run()
        } catch {
            stopProcess = nil
            sources.forEach {
                replaceLine("Could not start stop.sh: \(error.localizedDescription)\n", color: NSColor.systemRed, source: $0)
            }
            NSApp.terminate(nil)
        }
    }

    private func replaceLine(_ text: String, color: NSColor, source: Source) {
        guard let textView = textViews[source], let storage = textView.textStorage else { return }
        let shouldFollow = isNearBottom(source: source)
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 14, weight: .regular),
            .foregroundColor: color
        ]
        let attributed = NSAttributedString(string: text, attributes: attrs)
        let lineStart = lineStarts[source] ?? 0
        storage.replaceCharacters(in: NSRange(location: lineStart, length: storage.length - lineStart), with: attributed)
        if shouldFollow {
            textView.scrollRangeToVisible(NSRange(location: storage.length, length: 0))
        }
    }

    private func isNearBottom(source: Source) -> Bool {
        guard let scrollView = scrollViews[source], let textView = textViews[source] else { return true }
        let visible = scrollView.contentView.bounds
        let documentHeight = textView.bounds.height
        return documentHeight - visible.maxY < 24
    }
}

final class AppleASR {
    private let source: Source
    private let recognizer: SFSpeechRecognizer
    private let onText: (Source, String, Bool) -> Void
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var lastText = ""
    private var sessionID = 0
    private let lock = NSLock()

    init(source: Source, language: String, onText: @escaping (Source, String, Bool) -> Void) throws {
        self.source = source
        self.onText = onText
        let locale = Locale(identifier: language)
        guard let recognizer = SFSpeechRecognizer(locale: locale), recognizer.isAvailable else {
            throw NSError(domain: "MeetingHelper", code: 1, userInfo: [NSLocalizedDescriptionKey: "Apple Speech unavailable for \(language)"])
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
    }

    private func handle(sessionID: Int, result: SFSpeechRecognitionResult?, error: Error?) {
        lock.lock()
        let isCurrentSession = sessionID == self.sessionID
        lock.unlock()
        guard isCurrentSession else { return }

        if let error {
            fputs("Apple Speech error (\(source.rawValue)): \(error.localizedDescription)\n", stderr)
            restart(sessionID: sessionID)
            return
        }
        guard let result else { return }
        let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty && (result.isFinal || text != lastText) {
            onText(source, text, result.isFinal)
            lastText = text
        }
        if result.isFinal {
            restart(sessionID: sessionID)
        }
    }

    private func restart(sessionID: Int) {
        lock.lock()
        guard sessionID == self.sessionID else {
            lock.unlock()
            return
        }
        self.sessionID += 1
        task?.cancel()
        request?.endAudio()
        task = nil
        request = nil
        lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.startSession()
        }
    }
}

final class SubprocessASR {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let onText: (Source, String, Bool) -> Void
    private var stdoutBuffer = ""

    convenience init(script: String, arguments: [String] = [], onText: @escaping (Source, String, Bool) -> Void) throws {
        try self.init(command: ["/usr/bin/env", "python3", script] + arguments, onText: onText)
    }

    init(command: [String], onText: @escaping (Source, String, Bool) -> Void) throws {
        self.onText = onText
        process.executableURL = URL(fileURLWithPath: command[0])
        process.arguments = Array(command.dropFirst())
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.standardError

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.consume(handle.availableData)
        }
        try process.run()
    }

    deinit {
        output.fileHandleForReading.readabilityHandler = nil
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
        input.fileHandleForWriting.write(Data(line.utf8))
    }

    private func consume(_ data: Data) {
        guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
        stdoutBuffer.append(text)
        let parts = stdoutBuffer.split(separator: "\n", omittingEmptySubsequences: false)
        stdoutBuffer = parts.last.map(String.init) ?? ""
        for line in parts.dropLast() {
            if line.isEmpty { continue }
            guard
                let jsonData = line.data(using: .utf8),
                let obj = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
                let sourceRaw = obj["source"] as? String,
                let source = Source(rawValue: sourceRaw),
                let transcript = obj["text"] as? String
            else { continue }
            let final = obj["final"] as? Bool ?? true
            onText(source, transcript, final)
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
    private let queue = DispatchQueue(label: "meetinghelper.system-audio")
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

final class AppController: NSObject, NSApplicationDelegate {
    private let config: Config
    private let writer: TranscriptWriter
    private let debugRecorder: DebugRecorder?
    private var subtitle: SubtitleWindow?
    private var appleASR: [Source: AppleASR] = [:]
    private var pythonASR: SubprocessASR?
    private var workerASR: SubprocessASR?
    private var micCapture: MicCapture?
    private var systemCapture: SystemCapture?
    private var debugLevels: [Source: Float] = [:]
    private var lastDebugDraw = Date.distantPast

    init(config: Config) {
        self.config = config
        self.writer = TranscriptWriter(path: config.outputDir)
        self.debugRecorder = config.debug ? DebugRecorder(path: config.debugDir) : nil
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        subtitle = SubtitleWindow(config: config)
        if config.debug {
            subtitle?.showDebug(levels: debugLevels, sources: config.sourceMode.sources)
        } else {
            config.sourceMode.sources.forEach { subtitle?.setStatus("Listening...\n", source: $0) }
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
                title: "MeetingHelper could not start",
                message: error.localizedDescription,
                settingsURL: nil
            )
        }
    }

    private func requestPermissionsThenStart() {
        requestSpeechPermissionIfNeeded { [weak self] speechOK in
            guard let self else { return }
            guard speechOK else {
                self.showPermissionAlert(
                    title: "Speech Recognition permission needed",
                    message: "Enable Speech Recognition for live-subtitle, then start MeetingHelper again.",
                    settingsURL: "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition"
                )
                return
            }
            self.requestMicrophonePermissionIfNeeded { micOK in
                guard micOK else {
                    self.showPermissionAlert(
                        title: "Microphone permission needed",
                        message: "Enable Microphone access for live-subtitle, then start MeetingHelper again.",
                        settingsURL: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
                    )
                    return
                }
                self.start()
            }
        }
    }

    private func requestSpeechPermissionIfNeeded(_ completion: @escaping (Bool) -> Void) {
        guard config.asrMode == .apple else {
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
            if let source = self.config.sourceMode.sources.first {
                self.subtitle?.setStatus("\(title)\n", source: source)
            }
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
        switch config.asrMode {
        case .apple:
            var sources = config.sourceMode.sources
            if sources.count > 1 {
                // The Speech service serves one live recognition task per
                // process; run the system source in a worker subprocess.
                let executable = URL(fileURLWithPath: CommandLine.arguments[0]).path
                workerASR = try SubprocessASR(
                    command: [executable, "--speech-worker", "--language", config.language],
                    onText: handleText
                )
                sources.removeAll { $0 == .sys }
            }
            for source in sources {
                appleASR[source] = try AppleASR(source: source, language: config.language, onText: handleText)
            }
        case .hf:
            pythonASR = try SubprocessASR(script: config.hfScript, arguments: ["--hf-model", config.hfModel ?? ""], onText: handleText)
        case .sherpa:
            pythonASR = try SubprocessASR(script: config.sherpaScript, onText: handleText)
        }
    }

    private func startAudio() throws {
        let needsFloats = config.asrMode != .apple || config.debug || workerASR != nil
        let onFloats: ((Source, Double, [Float]) -> Void)? = needsFloats ? { [weak self] source, rate, floats in
            self?.handleFloats(source: source, sampleRate: rate, floats: floats)
        } : nil

        if config.sourceMode.sources.contains(.mic) {
            micCapture = MicCapture(
                onPCM: { [weak self] source, buffer in self?.appleASR[source]?.append(buffer) },
                onFloats: onFloats
            )
            try micCapture?.start()
        }
        if config.sourceMode.sources.contains(.sys) {
            systemCapture = SystemCapture(
                onSampleBuffer: { [weak self] source, buffer in self?.appleASR[source]?.append(buffer) },
                onFloats: onFloats
            )
            systemCapture?.start()
        }
    }

    private func handleFloats(source: Source, sampleRate: Double, floats: [Float]) {
        if config.asrMode != .apple {
            pythonASR?.send(source: source, sampleRate: sampleRate, floats: floats)
        } else if source == .sys {
            workerASR?.send(source: source, sampleRate: sampleRate, floats: floats)
        }

        guard let debugRecorder else { return }
        let level = debugRecorder.record(source: source, sampleRate: sampleRate, floats: floats)
        DispatchQueue.main.async {
            self.debugLevels[source] = level
            let now = Date()
            guard now.timeIntervalSince(self.lastDebugDraw) >= 0.15 else { return }
            self.lastDebugDraw = now
            self.subtitle?.showDebug(levels: self.debugLevels, sources: self.config.sourceMode.sources)
        }
    }

    private func handleText(source: Source, text: String, final: Bool) {
        DispatchQueue.main.async {
            self.subtitle?.update(text, source: source, final: final)
            if final {
                self.writer.append(source: source, text: text)
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
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

// Headless mode: the Speech service only serves one live recognition task per
// process, so `--source both` runs the system source in this worker subprocess.
// Protocol matches the Python workers: NDJSON audio frames in, NDJSON text out.
func runSpeechWorker(config: Config) -> Never {
    var asrs: [Source: AppleASR] = [:]
    var stdinBuffer = ""

    func emit(source: Source, text: String, final: Bool) {
        let payload: [String: Any] = ["source": source.rawValue, "text": text, "final": final]
        guard
            let json = try? JSONSerialization.data(withJSONObject: payload),
            let line = String(data: json, encoding: .utf8)
        else { return }
        FileHandle.standardOutput.write(Data((line + "\n").utf8))
    }

    FileHandle.standardInput.readabilityHandler = { handle in
        let data = handle.availableData
        if data.isEmpty { exit(0) }
        guard let text = String(data: data, encoding: .utf8) else { return }
        stdinBuffer.append(text)
        let parts = stdinBuffer.split(separator: "\n", omittingEmptySubsequences: false)
        stdinBuffer = parts.last.map(String.init) ?? ""
        for line in parts.dropLast() {
            if line.isEmpty { continue }
            guard
                let jsonData = line.data(using: .utf8),
                let obj = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
                let sourceRaw = obj["source"] as? String,
                let source = Source(rawValue: sourceRaw),
                let sampleRate = (obj["sampleRate"] as? NSNumber)?.doubleValue,
                let base64 = obj["pcmFloat32"] as? String,
                let pcm = Data(base64Encoded: base64)
            else { continue }
            if asrs[source] == nil {
                do {
                    asrs[source] = try AppleASR(source: source, language: config.language, onText: emit)
                } catch {
                    fputs("Speech worker could not start ASR: \(error.localizedDescription)\n", stderr)
                    exit(1)
                }
            }
            let floats = pcm.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
            if let buffer = pcmBuffer(sampleRate: sampleRate, floats: floats) {
                asrs[source]?.append(buffer)
            }
        }
    }
    RunLoop.main.run()
    exit(0)
}

var terminationSignals: [DispatchSourceSignal] = []
func installTerminationHandlers() {
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
if config.speechWorker {
    runSpeechWorker(config: config)
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppController(config: config)
app.delegate = delegate
installTerminationHandlers()
print("MeetingHelper subtitles running: source=\(config.sourceMode.rawValue), asr=\(config.asrMode.rawValue), output=\(config.outputDir)")
app.run()
