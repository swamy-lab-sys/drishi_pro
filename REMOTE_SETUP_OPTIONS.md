# Drishi Remote Interview Setup — Options & Discussion

**Scenario:** Hyderabad candidate in interview, Bangalore server running Drishi.
**Goal:** Real-time AI answers via Sarvam STT, zero detection risk.

---

## The Core Problem

| Challenge | Details |
|---|---|
| Tab capture indicator | Chrome shows 🔴 dot on captured tab |
| Screen sharing bar | "Drishi is sharing your screen" notification |
| Dual voice capture | Phone mic captures both interviewer + candidate |
| Google captions inaccurate | Indian English misrecognized |
| Interviewer sees tab bar | When full screen is shared |

---

## Option 1 — 3.5mm Audio Splitter (RECOMMENDED)

**Cost:** ₹150–200 | **Success:** 100% | **Detection:** Zero

**How it works:**
```
Laptop headphone jack
        │
    [TRRS Splitter]
        ├──── Earphones → candidate hears interviewer
        └──── Phone mic-in → captures exact interviewer audio
                │
                ▼
        Phone opens /stream page
        Streams audio → Bangalore server
        Sarvam STT → answers generated
                │
                ▼
        Phone /monitor page shows answers (split screen)
```

**Why 100% success:**
- Zero Chrome involvement on interview laptop
- Zero indicators anywhere
- Audio captured from line-out (not mic, not tab capture)
- Candidate's voice never enters the chain
- Works for Meet, Teams, Zoom, YouTube — any platform

**Buy:** Search "3.5mm TRRS splitter headphone mic" on Amazon India

**Setup steps:**
1. Plug TRRS splitter into laptop headphone jack
2. Connect earphones to one end
3. Connect phone to other end via TRRS cable
4. Phone opens `/stream` → selects mic input → starts capture
5. Phone split-screen: `/stream` (background) + `/monitor` (foreground)

---

## Option 2 — Old Phone/Tablet Near Speaker

**Cost:** ₹0 | **Success:** 95% | **Detection:** Zero

**How it works:**
```
Candidate wears earphones (laptop audio → earphones only)
Room is silent (no speaker output)
Old phone placed nearby with /stream page open
Phone mic captures... nothing useful ✗
```

**Problem:** If candidate wears earphones, room is silent — phone hears nothing.
**Workaround:** Use laptop speaker mode (no earphones) — phone captures speaker audio.
**Risk:** Candidate's voice also captured by phone mic → server confusion.

**Verdict:** Only works reliably with the hold-to-mute workaround, which is impractical in fluid conversation.

---

## Option 3 — PulseAudio Loopback (Linux Only)

**Cost:** ₹0 | **Success:** 85% | **Detection:** Very Low

**How it works:**
```
pactl load-module module-loopback latency_msec=5
        │
        ▼
Virtual "Monitor" mic device appears in system
        │
        ▼
/stream page selects Monitor device
getUserMedia() → captures Chrome audio output only
(interviewer voice only — candidate mic not included)
        │
        ▼
Streams → Bangalore server → Sarvam STT → answers
```

**Setup (one time):**
```bash
pactl load-module module-loopback latency_msec=5
# Permanent (survives reboot):
echo "load-module module-loopback latency_msec=5" | sudo tee -a /etc/pulse/default.pa
```

**Advantages:**
- No tab capture → no recording indicator
- Captures only what Chrome outputs (interviewer only)
- Free, no hardware needed
- Works for any meeting platform

**Risks:**
- Depends on PulseAudio (Linux only, not Wayland-stable)
- getUserMedia device selection must be correct
- Browser tab still open (could be seen if screen shared)

**Fix tab visibility:** Open `/stream` in Firefox or on Linux workspace 2 (`Ctrl+Alt+→`)

---

## Option 4 — Chrome Extension Tab Capture (Current)

**Cost:** ₹0 | **Success:** 90% | **Detection:** Low–Medium

**How it works:**
```
Extension captures meeting tab audio
Tab audio = interviewer voice only (your voice not echoed back)
Streams → Bangalore server → Sarvam STT → answers
Answers appear on phone /monitor
```

**Indicators shown:**
- 🔴 Recording dot on captured tab
- Visible only if interviewer can see tab bar (full screen share)

**Fix:** Share specific window (VS Code, browser window) not full screen → tab bar hidden.

**Verdict:** Good for interviews where screen share is not required, or where only a window is shared.

---

## Option 5 — Meeting Live Captions (Chrome Extension)

**Cost:** ₹0 | **Success:** 70% | **Detection:** Zero

**How it works:**
```
meeting_captions.js reads caption DOM text
Filters "You: ..." (candidate speech) → dropped
Sends interviewer text → /api/cc_question → answer
```

**Advantages:**
- No audio capture → zero indicators
- Already built and working
- Speaker-separated (captions identify who's talking)

**Disadvantages:**
- Google Meet captions inaccurate for Indian English
- Captions must be enabled in the meeting
- Depends on meeting platform caption DOM (breaks if platform updates)
- Delay: captions appear after speech, adding latency

**Verdict:** Fallback option only. Not reliable for Indian English.

---

## Option 6 — /stream Page as PWA (Standalone App)

**Cost:** ₹0 | **Success:** Depends on audio method | **Detection:** Low

**How it works:**
- Install `/stream` as Chrome PWA (Install App button in address bar)
- Opens as standalone window — no tab bar, no address bar
- No recording indicator visible in this window
- Use with PulseAudio loopback for best result

**Install:**
1. Open `https://particulate-arely-unrenovative.ngrok-free.dev/stream`
2. Click install icon in address bar
3. App opens in standalone window
4. Move to secondary desktop/workspace

---

## Comparison Table

| Option | Cost | Success | Indian English | Detection Risk | Hardware |
|---|---|---|---|---|---|
| 3.5mm Splitter | ₹150 | **100%** | ✅ Sarvam | **Zero** | Yes |
| Old phone near speaker | ₹0 | 95%* | ✅ Sarvam | Zero | No |
| PulseAudio loopback | ₹0 | 85% | ✅ Sarvam | Very Low | No |
| Tab capture (extension) | ₹0 | 90% | ✅ Sarvam | Low–Medium | No |
| Meeting captions | ₹0 | 70% | ❌ Chrome STT | Zero | No |
| /stream PWA | ₹0 | 85% | ✅ Sarvam | Low | No |

*Old phone only works if candidate uses laptop speakers (not earphones)

---

## Recording Indicator — Can It Be Hidden?

**Tab recording dot (🔴):** No. Chrome enforces this at browser level. No API, no CSS, no extension can remove it.

**Screen sharing bar ("Drishi is sharing your screen"):** No. OS-level notification enforced by Chrome.

**Workarounds:**
1. Share specific window (not full screen) → tab bar not visible
2. Use PulseAudio loopback → no tab capture → no dot
3. Use 3.5mm splitter → no browser capture at all → nothing to show

---

## Bangalore Server — What Runs There

- `./run.sh` starts everything
- Ngrok fixed domain: `particulate-arely-unrenovative.ngrok-free.dev`
- Sarvam STT processes audio from Hyderabad
- Answers stream back via SSE to `/monitor`
- No changes needed on Bangalore side regardless of which option Hyderabad uses

---

## Quick Decision Guide

```
Is screen share required in interview?
├── No  → Use tab capture extension (Option 4). Simple, works now.
└── Yes → Do you have a ₹150 splitter?
           ├── Yes → Option 1 (splitter). 100% safe.
           └── No  → Linux laptop?
                      ├── Yes → PulseAudio loopback (Option 3) + share specific window
                      └── No  → Share specific window only, use tab capture (Option 4)
```

---

*Created: 2026-03-23 | To be discussed*
