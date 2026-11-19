# Pending work

- Add resumable build-process sessions where the underlying build tool supports safe continuation.
- Detect dependencies more deeply in Unity/Unreal/Godot and Android SDK installations without assuming paths.
- Verify a real Android APK build when a compatible authorized SDK/project is available.
- Add translated agent status strings for all interface locales.
- Review the existing user `~/.codex/config.toml` warning about ignored legacy project `approval_policy` and `sandbox_mode` keys; the PortAI must not rewrite user Codex configuration.
