"""Điểm vào của NetWatchdog.exe (bản cài đặt) — cũng chạy được từ mã nguồn: pythonw app.py

  NetWatchdog.exe                    mở cửa sổ Cài đặt
  NetWatchdog.exe --service [...]    chạy theo dõi nền (Scheduled Task gọi); nhận thêm --dry-run, -v…
  NetWatchdog.exe --install-task     tạo Scheduled Task chạy nền khi đăng nhập (bộ cài gọi)
  NetWatchdog.exe --uninstall-task   dừng và xóa Scheduled Task (trình gỡ cài đặt gọi)
"""
import os
import sys


def service_command():
    """(exe, tham số) để Scheduled Task chạy chế độ nền."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--service"
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (exe if os.path.exists(exe) else sys.executable), f'"{os.path.abspath(__file__)}" --service'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd == "--service":
        import netwatchdog
        return netwatchdog.main(argv[1:])
    if cmd in ("--install-task", "--uninstall-task"):
        import netwatchdog
        import sysops
        netwatchdog.setup_logging()
        if cmd == "--install-task":
            ok, out = sysops.install_task(*service_command())
        else:
            sysops.stop_task()
            ok, out = sysops.uninstall_task()
        netwatchdog.log.info("%s: %s %s", cmd, ok, out)
        return 0 if ok else 1
    import settings_gui
    settings_gui.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
