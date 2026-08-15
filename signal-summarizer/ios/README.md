# Summarize, inside Signal iOS

A **Summarize** button in the conversation's navigation bar. Tap it, pick a
window, and a summary appears in a sheet — produced by Apple's on-device model.
No network call, no API key, no server, nothing leaves the phone.

This is a patch on top of [Signal-iOS](https://github.com/signalapp/Signal-iOS),
which you build and install yourself, and distribute to your own device through
TestFlight. **Read [Before you start](#before-you-start) and
[Getting the fork onto your Signal account](#getting-the-fork-onto-your-signal-account)
first — link the fork as a secondary device, do not register it, or your real
Signal app gets deactivated.**

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
  device — see [Distributing with TestFlight](#distributing-with-testflight).
- **Trademark.** Signal's name and logo are not covered by the AGPL. Rename the
  app and change the icon before uploading it anywhere.
- **Source.** The AGPL requires you to publish your modified source if you
  distribute the app.
- **Hardware.** Apple's on-device model needs an Apple Intelligence-capable
  device (iPhone 15 Pro or newer) on iOS 26+, with Apple Intelligence turned
  on. On anything older the button explains that instead of summarizing.

## Getting the fork onto your Signal account

**Link it as a secondary device. Do not register it.**

Signal allows one primary device per number. If you register the fork with your
own number it takes over as primary and your real Signal app is deactivated;
re-registering the real app then knocks the fork off. That is not the trade you
want.

Instead, on the fork's registration screen, open the menu on the phone-number
step and choose **"Link this device as a secondary device"**. The fork joins
your existing account the way Signal Desktop or an iPad does, and your real
Signal app stays primary and untouched.

This used to be iPad-only. On current upstream `main`,
`BuildFlags.linkedPhones` is `true`, and the entry point is gated on
`UIDevice.current.isIPad || BuildFlags.linkedPhones`
(`RegistrationPhoneNumberViewController.swift`), so the option is there on
iPhone with no patch from us. If a future upstream flips that flag off, setting
it back to `true` in `SignalServiceKit/Environment/BuildFlags.swift` is the
whole change.

Three things worth knowing:

- **You get history.** Provisioning runs Signal's link-and-sync
  (`LinkAndSyncManager` in `ProvisioningCoordinatorImpl`), so a newly linked
  device receives message history from the primary. The summarizer is not
  limited to messages that arrive after you install it.
- **Scanning the QR on the same phone is the awkward part.** The fork displays
  a QR code and the primary app has to scan it with the camera, which it cannot
  do on its own screen. Screenshot the QR, open the screenshot on a Mac or
  iPad, and scan it from Signal → Settings → Linked Devices. Tapping an
  `sgnl://linkdevice` link does *not* short-circuit this: `UrlOpener` shows a
  warning sheet and sends you to the Linked Devices screen to scan anyway.
- **No background push.** Signal's servers deliver pushes through Signal's own
  APNs credentials, which are tied to their bundle ID; your fork has yours, so
  it will not wake in the background. Messages queue on the server and sync
  when you open the fork — nothing is lost, but it is not a real-time client.
  For a summarizer you open on purpose, that is mostly fine.

Everything the summarizer reads works the same on a linked device: messages you
send from your primary phone arrive as outgoing messages in the fork's database
and appear in the transcript as "You".

## Distributing with TestFlight

TestFlight is the right call over free-account sideloading: builds last **90
days** instead of 7, so it is a quarterly rebuild rather than a weekly one.

- **Paid Apple Developer Program required** ($99/yr). Free accounts cannot use
  TestFlight at all.
- **Internal testers only.** Internal testers are members of your own App Store
  Connect team (up to 100), and internal builds do **not** go through Beta App
  Review. External testing does require review, and a Signal fork is not
  something to put in front of a reviewer — keep it internal.
- **It still needs an app record** in App Store Connect under your own bundle
  ID (set `SIGNAL_BUNDLEID_PREFIX`), with your own App Groups. Archive in Xcode
  and distribute to TestFlight; recent Xcode offers a "TestFlight Internal
  Only" distribution option that skips App Store distribution entirely.
- **Automated validation still runs** on upload — icons, entitlements, privacy
  manifest. Signal ships a `PrivacyInfo.xcprivacy`, so that part is covered.
- **Rename it.** The app name is visible throughout App Store Connect, and
  Signal's trademark is not licensed by the AGPL.

Apple changes these rules from time to time; check the current TestFlight
documentation before you rely on the specifics above.

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
