# SelfLock

**Open-source Android app blocker & website blocker for digital wellbeing, screen time control, and focus.**

SelfLock helps you break phone addiction by blocking distracting apps and websites on a schedule or daily time budget — with self-control locks so you can’t easily cheat.

[![Build APK](https://github.com/EtashTyagi/SelfLock/actions/workflows/build-apk.yml/badge.svg)](https://github.com/EtashTyagi/SelfLock/actions/workflows/build-apk.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Android%2014%2B-green.svg)](https://www.android.com/)
[![API](https://img.shields.io/badge/API-34%2B-brightgreen.svg)](https://developer.android.com/)
[![Kotlin](https://img.shields.io/badge/Kotlin-2.x-7F52FF.svg?logo=kotlin&logoColor=white)](https://kotlinlang.org/)
[![Jetpack Compose](https://img.shields.io/badge/Jetpack%20Compose-Material%203-4285F4.svg?logo=jetpackcompose&logoColor=white)](https://developer.android.com/jetpack/compose)

<p align="center">
  <b>App blocking · Website blocking · Daily limits · Schedules · Password locks · Usage stats</b>
</p>

---

## Why SelfLock?

Most screen-time and digital wellbeing tools are easy to disable the moment willpower fails. SelfLock is built for **self-control**:

| Problem | How SelfLock helps |
|--------|---------------------|
| Endless scrolling on social apps | Block apps by schedule or daily limit |
| YouTube / Reddit rabbit holes | Block websites in Chrome, Firefox, Edge, Samsung Internet |
| “Just five more minutes” | Active rules lock until the window ends or budget resets |
| Turning off the blocker yourself | Optional master password + per-rule passwords |
| No visibility into habits | Local usage statistics and block history |

**Privacy-first:** all rules, usage, and passwords stay on your device. No accounts, no cloud sync, no analytics SDKs.

---

## Features

### App blocker (Android)
- Block any installed app by **time-of-day schedule** or **daily usage limit**
- Full-screen block overlay when a restricted app is opened
- Foreground detection via Accessibility Service + Usage Stats backup
- App icons in rule lists and block screen

### Website blocker
- Block domains (e.g. `youtube.com`, `reddit.com`) by schedule or daily budget
- Works in **Chrome, Firefox, Samsung Internet, and Edge** via URL-bar detection
- No root required; accessibility-based (no VPN required)
- Favicons on rules and block overlay

### Self-control & security
- **Locked rules** while a block is active — can’t edit/delete mid-session
- Optional **master password** (app lock on launch/resume)
- **Per-rule passwords** with master password as override
- PBKDF2-HMAC-SHA256 password hashing (600k iterations) + EncryptedSharedPreferences
- `FLAG_SECURE` when app lock is enabled

### Usage tracking & stats
- Daily usage accumulation for apps and matched website rules
- Statistics tab with summaries and date range selection
- Budget warnings and block notifications

### Setup & reliability
- Guided **onboarding** for all required permissions
- Foreground monitoring service + boot/schedule receivers
- Exact alarms for schedule start/end
- Battery optimization guidance for reliable enforcement

---

## How it works

```
┌─────────────────┐     ┌──────────────────────┐     ┌────────────────────┐
│ Accessibility   │────▶│ Rule engine          │────▶│ Block overlay      │
│ + Usage Stats   │     │ (schedule / limit)   │     │ (full-screen)      │
└─────────────────┘     └──────────────────────┘     └────────────────────┘
         │                        │
         ▼                        ▼
   Browser URL bar          Room database
   (website domains)        (rules, usage, events)
```

1. **Apps** — detects the foreground package; if a rule is active, shows a non-dismissible block screen.
2. **Websites** — reads the browser address bar, matches the domain against your rules, blocks when over schedule/limit.
3. **Self-lock** — while a rule is in its active window or daily budget is exhausted, the rule stays read-only unless unlocked with a password.

> **Note:** Website blocking is accessibility-based (not VPN). Ideal if you already use a VPN (e.g. Tailscale) and don’t want a second VPN profile.

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Language | Kotlin |
| UI | Jetpack Compose, Material 3 |
| Architecture | MVVM + Clean Architecture |
| DI | Hilt |
| Database | Room |
| Async / background | Coroutines, Foreground Service, AlarmManager |
| Security | AndroidX Security Crypto, PBKDF2 |
| Images | Coil (favicons) |

**Requirements:** Android 14+ (API 34), target API 36 · tested with modern Pixel devices.

---

## Screenshots

> Screenshots welcome — open a PR with images under `docs/screenshots/` and link them here.

| Websites | Apps | Statistics | Block overlay |
|----------|------|------------|---------------|
| *Coming soon* | *Coming soon* | *Coming soon* | *Coming soon* |

---

## Getting started

### Build from source

```bash
git clone https://github.com/EtashTyagi/SelfLock.git
cd SelfLock
./gradlew assembleDebug
```

Debug APK:

```text
app/build/outputs/apk/debug/app-debug.apk
```

Release (minified):

```bash
./gradlew assembleRelease
```

### Install on a device

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Or open the project in **Android Studio** (Ladybug / recent stable) and Run.

### First-run setup

On first launch, complete onboarding and grant:

1. **Accessibility** — foreground app + browser URL detection  
2. **Usage access** — app usage timing  
3. **Display over other apps** — block overlay  
4. **Notifications** — monitoring & budget alerts  
5. **Exact alarms** — schedule boundaries  
6. **Battery unrestricted** — reliable background enforcement  

Optional: set a **master password** so SelfLock itself can’t be opened freely.

---

## Usage

1. Open the **Apps** or **Websites** tab.
2. Add a rule:
   - **Schedule** — blocked between start and end time (per day of week as configured).
   - **Daily limit** — allowed minutes per day; blocked after the budget is used.
3. While the rule is active, SelfLock shows the block screen and locks the rule from easy edits.
4. Check **Statistics** for usage over time.
5. Use **Settings** (gear icon) for theme, notifications, and password options.

---

## Project structure

```text
app/src/main/java/com/selflock/app/
├── data/           # Room entities, DAOs, repositories
├── domain/         # Models + use cases (block status, lock, installed apps)
├── di/             # Hilt modules
├── security/       # Master password, app lock state
├── service/        # Accessibility, monitoring, boot/schedule receivers
├── ui/             # Compose screens, components, navigation, theme
└── util/           # Permissions, URL parsing, schedule helpers
```

---

## Permissions (what & why)

| Permission | Why |
|------------|-----|
| Accessibility Service | Detect foreground app; read browser URL bars for website rules |
| Usage access (`PACKAGE_USAGE_STATS`) | Measure app time for daily limits |
| Display over other apps | Show the full-screen block UI |
| Notifications | Monitoring status, blocks, budget warnings |
| Foreground service | Continuous monitoring without being killed |
| Exact alarms | Fire at schedule start/end |
| Boot completed | Resume protection after reboot |
| Query all packages | List apps when creating rules |
| Internet | Load website favicons only |

SelfLock does **not** send your browsing or app usage to a server.

---

## Privacy

- **100% on-device** rules, logs, and passwords  
- No login, no telemetry, no third-party ad/analytics SDKs  
- Password hashes stored with system-backed encrypted preferences  
- Open source — audit the code yourself  

---

## Contributing

Contributions are welcome: bug fixes, browser URL-bar mappings, UI polish, docs, and translations.

1. Fork the repo  
2. Create a branch: `git checkout -b feature/your-feature`  
3. Commit with a clear message  
4. Open a Pull Request against `master`  

Please keep changes focused. Match existing Kotlin / Compose style (see `AGENTS.md` for contributor-oriented project notes).

### Ideas for contributors

- [ ] More browser / in-app browser URL detectors  
- [ ] Home-screen widget (remaining budget)  
- [ ] Export/import rules (local file)  
- [ ] Additional languages  
- [ ] Play Store / F-Droid packaging notes  
- [ ] Instrumented tests for rule locking & schedule edges  

---

## Roadmap & limitations

**Current focus**
- Reliable app + website enforcement without a local VPN  
- Strong self-lock (passwords + locked active rules)  
- Clear Material 3 UI  

**Known limitations**
- Website blocking depends on accessibility URL-bar IDs (browsers can change them)  
- Not a network-level DNS sinkhole (no VPN path in the active design)  
- Cannot fully prevent Safe Mode / ADB uninstall / factory reset  
- minSdk 34 — Android 14 and newer only  

---

## FAQ

**Is this on Google Play?**  
Build from source for now. Releases may appear via GitHub Actions artifacts.

**Does it need root?**  
No.

**Can I use my own VPN?**  
Yes. SelfLock does not require the Android VPN slot for website blocking.

**Will it drain battery?**  
It runs a lightweight foreground monitor. Exempting the app from battery optimization improves reliability.

**Is my data private?**  
Yes — local-only. See [Privacy](#privacy).

---

## Star History

If SelfLock helps you stay focused, consider starring the repo — it helps others discover open-source digital wellbeing tools.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Acknowledgments

- [Jetpack Compose](https://developer.android.com/jetpack/compose) & Material 3  
- [Hilt](https://dagger.dev/hilt/) · [Room](https://developer.android.com/training/data-storage/room) · [Coil](https://coil-kt.github.io/coil/)  

---

<p align="center">
  <sub>
    Keywords: Android app blocker, website blocker, screen time, digital wellbeing,
    focus mode, phone addiction, usage limits, open source Kotlin Jetpack Compose
  </sub>
</p>
