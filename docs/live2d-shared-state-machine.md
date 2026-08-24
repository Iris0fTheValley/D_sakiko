# Shared Live2D state machine

Electron mode is selected by default. Set `DSAKIKO_RENDERER=pygame` only when
the legacy Pygame renderer is explicitly needed for compatibility.

```text
Python Live2DBehaviorController
  ├─ play_motion {group, index, priority, token}
  ├─ play_audio {url, token, segment_id}
  └─ thinking / model / close commands
              │
       bridge/protocol.py
              │ WebSocket broadcast
       Electron command executor(s)
              │
       motion_started / motion_finished / audio_ended
```

The controller is the only owner of behavior state, timers, motion selection,
and segment lifecycle. Electron does not select random indexes or infer motion
completion from a Promise. It listens to the Live2D SDK motion manager's
`motionFinish` event, plays the specified audio, performs lip sync, and reports
facts back through the bridge. The bridge only transports versioned messages;
it does not inspect legacy queues or rewrite motion groups.

Generated audio is exposed only through the bridge's constrained local HTTP
server on port `9877`. The WebSocket control channel uses port `9876`.
