# BearBless Bridge

Android companion used only for display-scoped Unicode text entry. It does not
use the clipboard or an IME. The exported provider accepts calls only from the
ADB shell/root UID; the accessibility service refuses Display 0.

Build and install from Android Studio, then enable **BearBless 虚拟屏输入桥**
under Android Accessibility settings. Verify it with:

```bash
adb shell content call --uri content://com.bearbless.bridge.control --method health
```

Expected output contains `enabled=true`.
