import AVFoundation
import Foundation
import Speech

func waitUntil(timeout: TimeInterval, _ done: () -> Bool) -> Bool {
    let deadline = Date(timeIntervalSinceNow: timeout)
    while !done(), deadline.timeIntervalSinceNow > 0 {
        RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.1))
    }
    return done()
}

var args = Array(CommandLine.arguments.dropFirst())
guard !args.isEmpty else {
    fputs("Usage: transcribe-audio <audio-file> [--language en-US] [--output file.txt]\n", stderr)
    exit(2)
}

let input = args.removeFirst()
var language = "en-US"
var output: String?
while !args.isEmpty {
    let option = args.removeFirst()
    guard !args.isEmpty else {
        fputs("Missing value for \(option)\n", stderr)
        exit(2)
    }
    let value = args.removeFirst()
    switch option {
    case "--language": language = value
    case "--output": output = value
    default:
        fputs("Unknown option: \(option)\n", stderr)
        exit(2)
    }
}

let inputURL = URL(fileURLWithPath: input).standardizedFileURL
guard FileManager.default.fileExists(atPath: inputURL.path) else {
    fputs("Audio file not found: \(inputURL.path)\n", stderr)
    exit(1)
}

var authorizationStatus: SFSpeechRecognizerAuthorizationStatus?
SFSpeechRecognizer.requestAuthorization { status in
    authorizationStatus = status
}
guard waitUntil(timeout: 30, { authorizationStatus != nil }) else {
    fputs("Timed out waiting for Speech Recognition permission.\n", stderr)
    exit(1)
}
guard authorizationStatus == .authorized else {
    fputs("Speech Recognition permission is required. Enable it in System Settings > Privacy & Security.\n", stderr)
    exit(1)
}

guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: language)), recognizer.isAvailable else {
    fputs("Apple Speech is unavailable for language: \(language)\n", stderr)
    exit(1)
}

let request = SFSpeechURLRecognitionRequest(url: inputURL)
request.shouldReportPartialResults = false
request.addsPunctuation = true
var transcript: String?
var failure: Error?
var finished = false
let task = recognizer.recognitionTask(with: request) { result, error in
    if let error {
        failure = error
        finished = true
    } else if let result, result.isFinal {
        transcript = result.bestTranscription.formattedString
        finished = true
    }
}
let audio = try? AVAudioFile(forReading: inputURL)
let duration = audio.map { Double($0.length) / $0.fileFormat.sampleRate } ?? 0
let timeout = min(3600, max(60, duration * 3 + 30))
guard waitUntil(timeout: timeout, { finished }) else {
    task.cancel()
    fputs("Transcription timed out after \(Int(timeout)) seconds.\n", stderr)
    exit(1)
}
withExtendedLifetime(task) {}

if let failure {
    fputs("Transcription failed: \(failure.localizedDescription)\n", stderr)
    exit(1)
}
guard let transcript, !transcript.isEmpty else {
    fputs("No speech detected.\n", stderr)
    exit(1)
}

if let output {
    let outputURL = URL(fileURLWithPath: output).standardizedFileURL
    try FileManager.default.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)
    try (transcript + "\n").write(to: outputURL, atomically: true, encoding: .utf8)
    print(outputURL.path)
} else {
    print(transcript)
}
