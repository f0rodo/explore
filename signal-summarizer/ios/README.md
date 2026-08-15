# Summarize, inside Signal iOS

A **Summarize** button in the conversation's navigation bar. Tap it, pick a
window, and a summary appears in a sheet — produced by Apple's on-device model.
No network call, no API key, no server, nothing leaves the phone.

This is a patch on top of [Signal-iOS](https://github.com/signalapp/Signal-iOS),
which you build and install yourself. **Read [Before you start](#before-you-start)
— this cannot be shipped on the App Store, and it needs re-signing every 7 days
on a free Apple account.**

## Why a fork rather than a keyboard

A custom keyboard extension can only read the text field it is typing into
(`UITextDocumentProxy.documentContextBeforeInput`/`After`) — the compose box.
iOS gives no third-party API for reading another app's views, so a keyboard
cannot see the conversation above it, with or without Full Access. Same for
share and action extensions: they get what the user explicitly hands them.

Signal iOS is AGPL-3.0, so the app itself can be changed — that is the only
design where "hit summarize and it summarizes the chat" actually works.

## Before you start

- **No App Store.** Apple will not accept a Signal fork, and Signal's own terms
  do not permit third-party clients on their servers. This is for your own
  device.
- **Signing.** A free Apple ID signs an app for **7 days**, then it stops
  launching until you rebuild. A paid developer account ($99/yr) gets a year.
- **Trademark.** Signal's name and logo are not covered by the AGPL. If you
  distribute this to anyone else, rename it and change the icon.
- **Source.** The AGPL requires you to publish your modified source if you
  distribute the app.
- **Registration.** Push notifications will not work on a third-party build
  (Signal's certificate). Register or link the fork as its own device.
- **Hardware.** Apple's on-device model needs an Apple Intelligence-capable
  device (iPhone 15 Pro or newer) on iOS 26+, with Apple Intelligence turned
  on. On anything older the button explains that instead of summarizing.

## What is in here

| Path | What it is |
| --- | --- |
| `Sources/SummarizerCore/` | Prompts, transcript rendering, chunking, map-reduce. Plain Swift — no Signal, no Apple frameworks except `FoundationModels`. |
| `Tests/SummarizerCoreTests/` | Tests for the above. `swift test` from this directory. |
| `SignalPatch/SignalTranscriptSource.swift` | The only file that touches Signal internals: reads a window of a thread out of its database. |
| `SignalPatch/SummaryViewController.swift` | The summary sheet. |
| `SignalPatch/ConversationViewController+Summarize.swift` | The button and what it does. |
| `SignalPatch/navbar.patch` | One-line change that puts the button in the navigation bar. |

## Build it

```bash
# 1. Fork and clone Signal-iOS (submodules matter)
git clone --recurse-submodules https://github.com/signalapp/Signal-iOS
cd Signal-iOS
make dependencies

# 2. Drop in the summarizer
mkdir -p Signal/Summarizer
cp -R /path/to/signal-summarizer/ios/Sources/SummarizerCore/*.swift Signal/Summarizer/
cp /path/to/signal-summarizer/ios/SignalPatch/*.swift Signal/Summarizer/

# 3. Add the button to the navigation bar
git apply /path/to/signal-summarizer/ios/SignalPatch/navbar.patch

# 4. Build
open Signal.xcworkspace
```

In Xcode:

1. Add the files in `Signal/Summarizer/` to the **Signal** target (drag the
   folder into the project navigator, tick "Signal" under Target Membership).
   The core files are compiled into the app target directly, so no `import
   SummarizerCore` is needed. If you would rather consume them as a package,
   add `ios/` as a local Swift package and add the import to the three
   `SignalPatch` files.
2. Set your own **Team** on every target (Signal, SignalShareExtension,
   SignalNSE), and set `SIGNAL_BUNDLEID_PREFIX` in the project settings to
   something of your own.
3. Turn **off** Push Notifications, Apple Pay, Communication Notifications and
   Data Protection in Signing & Capabilities. Keep **App Groups** and
   **Background Modes** on.
4. Build and run on the device.

`BUILDING.md` in Signal-iOS is the authority on the general build; the steps
above are only the parts specific to this patch.

## Test the summarizer on its own

The logic that does not need a device or a model has tests:

```bash
cd ios
swift test
```

That covers transcript rendering, chunking, the map-reduce fold, and that the
progress estimate matches the number of model calls actually made.

## How it behaves

- **Windows.** The button offers the last 12 hours, the last 24 hours, or the
  last 50 messages, capped at 300 messages per summary.
- **Chunking.** Apple's on-device model has a small context window, so a long
  window is summarized in passes: each chunk separately, then the partial
  summaries are combined. The sheet shows "pass 2 of 5" while that happens.
- **What is read.** Incoming and outgoing message text in the open
  conversation, plus the text of a quoted reply. Deleted and view-once messages
  are skipped, and so are system events ("X joined the group"). Attachments are
  not described — that needs the attachment store and is not wired up.
- **What is stored.** Nothing. The transcript is read at the moment you tap the
  button and lives only in memory; the summary is not saved or sent anywhere
  unless you copy it out.

## Swapping the model

`SummarizationEngine` is a one-method protocol:

```swift
public protocol SummarizationEngine: Sendable {
    func respond(instructions: String, prompt: String) async throws -> String
}
```

`FoundationModelsEngine` is the shipped implementation. To use llama.cpp, MLX,
or a Core ML model instead, write another conformance and pass it to
`ConversationSummarizer(engine:)` — the prompts, chunking, and UI are unchanged.
That is also the way to support devices without Apple Intelligence.
