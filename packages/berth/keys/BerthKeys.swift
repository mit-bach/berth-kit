// berth-keys: Berth's keyboard shortcuts in cmux, handled outside the terminal (2026-10-31).
//
// ⌘⇧X dismisses the follow-up of the workspace selected in cmux: it runs
// `berth --json follow-up selected --dismiss` in the background. Nothing is typed
// into a terminal and no tab opens. The key is held only while cmux is the
// frontmost app, so every other app keeps ⌘⇧X. Carbon hot keys need no
// Accessibility or Input Monitoring permission.
//
// Berth's watcher starts this and keeps it running (berth/keys.py). It exits
// when its stdin closes, which is when the watcher goes. SIGUSR1 runs the same
// action as the key, for a check without a keyboard. Lines on stderr are its log.

import AppKit
import Carbon.HIToolbox

let berthPath = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : (NSString(string: "~/.local/bin/berth").expandingTildeInPath as String)
let cmuxBundle = "com.cmuxterm.app"
var hotKeyRef: EventHotKeyRef?

func log(_ text: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardError.write("\(stamp) \(text)\n".data(using: .utf8)!)
}

func dismiss(_ how: String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: berthPath)
    process.arguments = ["--json", "follow-up", "selected", "--dismiss"]
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
        try process.run()
        log("dismiss (\(how))")
    } catch {
        log("dismiss failed: \(error)")
    }
}

func arm() {
    guard hotKeyRef == nil else { return }
    let id = EventHotKeyID(signature: OSType(0x4252_5448), id: 1)  // 'BRTH'
    let status = RegisterEventHotKey(UInt32(kVK_ANSI_X), UInt32(cmdKey | shiftKey), id,
                                     GetApplicationEventTarget(), 0, &hotKeyRef)
    log(status == noErr ? "armed: cmd+shift+x" : "could not take cmd+shift+x (status \(status))")
}

func disarm() {
    guard let ref = hotKeyRef else { return }
    UnregisterEventHotKey(ref)
    hotKeyRef = nil
}

func follow() {
    if NSWorkspace.shared.frontmostApplication?.bundleIdentifier == cmuxBundle { arm() } else { disarm() }
}

var pressed = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
    dismiss("cmd+shift+x")
    return noErr
}, 1, &pressed, nil, nil)

NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main
) { _ in follow() }

signal(SIGUSR1, SIG_IGN)
let check = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
check.setEventHandler { dismiss("SIGUSR1") }
check.resume()

FileHandle.standardInput.readabilityHandler = { handle in
    if handle.availableData.isEmpty { exit(0) }  // the watcher went
}

follow()
log("running for \(berthPath)")
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
app.run()
