import Foundation

#if canImport(FoundationModels)
import FoundationModels

/// Apple's on-device model (iOS 26 / macOS 26, Apple Intelligence hardware).
///
/// Nothing leaves the device: no network call, no model download, no API key.
///
/// If this file is the only thing that fails to build, the framework's API
/// moved — everything else here is plain Swift. Replace it with any other
/// `SummarizationEngine` (llama.cpp, MLX, Core ML) and the rest is unchanged.
@available(iOS 26.0, macOS 26.0, *)
public struct FoundationModelsEngine: SummarizationEngine {
    public enum Availability {
        case available
        case unavailable(String)
    }

    public init() {}

    public static var availability: Availability {
        switch SystemLanguageModel.default.availability {
        case .available:
            return .available
        case .unavailable(let reason):
            switch reason {
            case .deviceNotEligible:
                return .unavailable("This device does not support Apple Intelligence.")
            case .appleIntelligenceNotEnabled:
                return .unavailable("Turn on Apple Intelligence in Settings to summarize.")
            case .modelNotReady:
                return .unavailable("The on-device model is still downloading. Try again shortly.")
            @unknown default:
                return .unavailable("The on-device model is unavailable right now.")
            }
        @unknown default:
            return .unavailable("The on-device model is unavailable right now.")
        }
    }

    public func respond(instructions: String, prompt: String) async throws -> String {
        if case .unavailable(let reason) = Self.availability {
            throw SummarizerError.engineUnavailable(reason)
        }
        // A fresh session per call: each chunk is summarized independently, and
        // a short context keeps us clear of the on-device window.
        let session = LanguageModelSession(instructions: instructions)
        let response = try await session.respond(to: prompt)
        return response.content
    }
}
#endif
