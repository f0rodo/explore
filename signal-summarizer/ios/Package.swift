// swift-tools-version: 6.0
import PackageDescription

// Standalone package so the summarization logic can be built and tested with
// `swift test` without opening the Signal workspace. The Signal fork consumes
// the same sources — see ios/README.md.
let package = Package(
    name: "SummarizerCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "SummarizerCore", targets: ["SummarizerCore"])
    ],
    targets: [
        .target(name: "SummarizerCore"),
        .testTarget(name: "SummarizerCoreTests", dependencies: ["SummarizerCore"]),
    ]
)
