# NetWatchdog: giữ máy Windows luôn có mạng

Chương trình chạy nền trên Windows 10/11. Nó kiểm tra Internet định kỳ, và khi **rớt mạng** sẽ tự xử lý theo thứ tự:

1. **Thử kết nối các mạng Wi-Fi dự phòng** đã chọn (lần lượt theo thứ tự ưu tiên).
2. **Khởi động lại (tắt/bật) các card mạng** đã chọn.
3. Lặp lại bước 1–2 theo số vòng cấu hình.
4. Vẫn không được thì **khởi động lại máy**. Khi máy lên lại, chương trình **mở các ứng dụng đã chọn**.

Mọi sự kiện được **báo lên Telegram**: mất mạng, đã thử những gì, có mạng lại (mất bao lâu, khôi phục bằng cách nào), khởi động lại máy, ứng dụng đã mở. Tin phát sinh lúc mất mạng được lưu ra đĩa và **gửi bù khi có mạng**, kể cả sau khi khởi động lại máy. Ngoài ra có báo cáo định kỳ: uptime, CPU, RAM, ổ đĩa, pin, Wi-Fi, IP LAN/WAN.

## Chạy nhanh bằng file .bat (không cần cài)

Cần có **Python 3.10+** (<https://www.python.org/downloads/>, tick **"Add python.exe to PATH"**). Sau đó:

| File | Tác dụng |
|---|---|
| **`NetWatchdog.bat`** | Nhấp đúp để chạy ngay ở chế độ nền, tự xin quyền Admin. Lần đầu sẽ tự cài `psutil`, tạo `config.json` và mở cửa sổ Cài đặt. |
| `CaiDat.bat` | Mở cửa sổ Cài đặt (Telegram, Wi-Fi, card mạng, ứng dụng). |
| `DungLai.bat` | Dừng NetWatchdog đang chạy nền. |
| `run_dryrun.bat` | Chạy thử ở cửa sổ console, chỉ ghi log, không thao tác thật. |

Cách này chỉ chạy đến khi tắt máy. Muốn **tự chạy mỗi lần mở máy** (bắt buộc để tự mở ứng dụng sau khi khởi động lại), hãy cài theo mục dưới. Có thể bỏ `NetWatchdog.bat` vào `shell:startup`, nhưng khi đó Windows sẽ hỏi UAC mỗi lần đăng nhập.

## Cài đặt (tự chạy cùng Windows)

1. Cài **Python 3.10+** từ <https://www.python.org/downloads/> và tick **"Add python.exe to PATH"**.
2. Tải repo về (hoặc chỉ thư mục `netwatchdog/`) và đặt ở chỗ cố định, ví dụ `C:\Tools\netwatchdog`.
3. Chuột phải **`install.ps1` → Run with PowerShell** và đồng ý quyền Administrator. Script sẽ:
   - cài `psutil`;
   - tạo Scheduled Task **NetWatchdog** chạy nền với quyền cao nhất mỗi khi đăng nhập (tự chạy lại nếu bị tắt);
   - tạo lối tắt **"NetWatchdog - Cai dat"** trên Desktop;
   - mở cửa sổ **Cài đặt**.
4. Trong cửa sổ Cài đặt:
   - **Telegram**: tạo bot qua [@BotFather](https://t.me/BotFather) (`/newbot`) rồi dán token. Mở bot, gửi `/start`, bấm **Lấy Chat ID**, sau đó bấm **Gửi tin thử**.
   - **Wi-Fi dự phòng**: chuyển các mạng muốn thử sang cột phải và sắp xếp thứ tự. Chỉ chọn được mạng mà máy đã từng kết nối và đã lưu mật khẩu.
   - **Card mạng**: chọn các card sẽ được tắt/bật lại.
   - **Khởi động lại & Ứng dụng**: bật/tắt tự khởi động lại, giới hạn số lần mỗi ngày, danh sách ứng dụng cần mở.
   - Bấm **Lưu**. Chương trình đang chạy sẽ tự áp dụng, không cần khởi động lại.

Gỡ cài đặt: chạy `uninstall.ps1`.

### ⚠️ Bắt buộc để mở được ứng dụng sau khi khởi động lại

NetWatchdog chạy khi bạn **đăng nhập**, nên máy phải **tự đăng nhập Windows** sau khi khởi động lại:

- Cách 1: dùng [Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) của Microsoft (mật khẩu được lưu mã hóa).
- Cách 2: `Win+R` → `netplwiz` → bỏ tick *"Users must enter a user name and password…"*. Trên Windows 11, nếu không thấy ô này, hãy tắt *Settings → Accounts → Sign-in options → "For improved security, only allow Windows Hello sign-in…"*.

## Lệnh Telegram

Bot chỉ nhận lệnh từ đúng Chat ID đã cấu hình:

| Lệnh | Tác dụng |
|---|---|
| `/status` | Báo cáo tình trạng máy |
| `/apps` | Mở các ứng dụng đã chọn |
| `/fix` | Chạy quy trình khôi phục mạng ngay |
| `/reboot yes` | Khởi động lại máy sau 30 giây |
| `/cancel` | Hủy lệnh khởi động lại đang chờ |

## Cơ chế an toàn

- Chỉ coi là mất mạng sau **N lần kiểm tra lỗi liên tiếp** (mặc định 3 × 30 giây). Có mạng khi kết nối TCP được tới **bất kỳ** đích nào (1.1.1.1:53, 8.8.8.8:53, …) hoặc URL HTTP kiểm tra trả về OK.
- **Giới hạn số lần khởi động lại trong 24 giờ** (mặc định 3) để tránh vòng lặp reboot khi nhà mạng sập lâu.
- **Không khởi động lại khi máy vừa bật** chưa đủ X phút (mặc định 15).
- Đếm ngược trước khi reboot (mặc định 60 giây). Muốn hủy thì gõ `shutdown /a` hoặc gửi `/cancel`.
- Sau một lần khôi phục thất bại mà không reboot, chương trình nghỉ X phút rồi mới thử lại, để không tắt/bật card liên tục.

## Chạy thử / gỡ lỗi

- `run_dryrun.bat`: chạy ở cửa sổ console, **chỉ ghi log** (không đổi mạng, không khởi động lại, không mở app).
- `python netwatchdog.py --status`: in báo cáo tình trạng máy.
- `python netwatchdog.py --test-telegram`: gửi tin thử.
- Log nằm ở `logs\netwatchdog.log`. Trạng thái (hàng đợi tin, lịch sử reboot) nằm ở `state.json`.
- Test: `python -m unittest discover -s tests`.

## Cấu trúc

| File | Vai trò |
|---|---|
| `netwatchdog.py` | Vòng lặp theo dõi, quy trình khôi phục, Telegram (gửi + hàng đợi + lệnh), báo cáo |
| `sysops.py` | Thao tác Windows: `netsh wlan`, `Disable/Enable-NetAdapter`, `shutdown`, mở ứng dụng |
| `settings_gui.py` | Cửa sổ Cài đặt (Tkinter), ghi `config.json` |
| `config.example.json` | Cấu hình mẫu. `config.json` thật chứa bot token nên **không commit** (đã có trong `.gitignore`) |
| `install.ps1` / `uninstall.ps1` | Cài/gỡ Scheduled Task và lối tắt |
| `NetWatchdog.bat` / `CaiDat.bat` / `DungLai.bat` | Chạy nhanh / mở Cài đặt / dừng, không cần cài |
