# NetWatchdog: giữ máy Windows luôn có mạng

Chương trình chạy nền trên Windows 10/11. Nó kiểm tra Internet định kỳ, và khi **rớt mạng** sẽ tự xử lý theo thứ tự:

1. **Thử kết nối các mạng Wi-Fi dự phòng** đã chọn (lần lượt theo thứ tự ưu tiên).
2. **Khởi động lại (tắt/bật) các card mạng** đã chọn.
3. Lặp lại bước 1–2 theo số vòng cấu hình.
4. Vẫn không được thì **khởi động lại máy**. Khi máy lên lại, chương trình **mở các ứng dụng đã chọn**.

Mọi sự kiện được **báo lên Telegram**: mất mạng, đã thử những gì, có mạng lại (mất bao lâu, khôi phục bằng cách nào), khởi động lại máy, ứng dụng đã mở. Tin phát sinh lúc mất mạng được lưu ra đĩa và **gửi bù khi có mạng**, kể cả sau khi khởi động lại máy. Ngoài ra có báo cáo định kỳ: uptime, CPU, RAM, ổ đĩa, pin, Wi-Fi, IP LAN/WAN.

Tùy chọn **theo dõi jxtdAuto** (tool auto Võ Lâm): báo cáo các nhân vật và cảnh báo khi có sự cố, xem [bên dưới](#theo-dõi-jxtdauto).

## Cài đặt

1. Tải **`NetWatchdog-Setup-x.y.z.exe`** ở trang [Releases](https://github.com/AdminReboot/MyPC/releases/latest) và chạy (cần quyền Administrator). Không cần cài Python.
   - Windows có thể hiện cảnh báo SmartScreen vì file chưa ký số: bấm **More info → Run anyway**.
2. Bộ cài sẽ chép chương trình vào `C:\Program Files\NetWatchdog`, tạo tác vụ **NetWatchdog** chạy nền với quyền cao nhất mỗi khi đăng nhập (tự chạy lại nếu bị tắt), thêm lối tắt **NetWatchdog** vào Start Menu (tùy chọn thêm ra Desktop).
3. Mở **NetWatchdog** để cài đặt (giao diện tự theo chế độ Sáng/Tối của Windows):
   - **Tổng quan**: tình trạng mạng, NetWatchdog có đang chạy nền không (nút **Bật/Tắt chạy nền**), CPU/RAM/ổ đĩa/pin, IP, số lần reboot và tin Telegram chờ gửi. Tự làm mới mỗi 30 giây.
   - **Telegram**: tạo bot qua [@BotFather](https://t.me/BotFather) (`/newbot`) rồi dán token. Mở bot, gửi `/start`, bấm **Lấy Chat ID**, sau đó bấm **Gửi tin thử**.
   - **Wi-Fi dự phòng**: chuyển các mạng muốn thử sang cột phải và sắp xếp thứ tự. Chỉ chọn được mạng mà máy đã từng kết nối và đã lưu mật khẩu.
   - **Card mạng**: chọn các card sẽ được tắt/bật lại.
   - **Khởi động lại & Ứng dụng**: bật/tắt tự khởi động lại, giới hạn số lần mỗi ngày, danh sách ứng dụng cần mở.
   - Bấm **Lưu thay đổi** (Ctrl+S). Chương trình đang chạy nền sẽ tự áp dụng, không cần khởi động lại.

Cấu hình, trạng thái và log nằm ở `%LOCALAPPDATA%\NetWatchdog` nên **cập nhật hay gỡ cài đặt đều không mất cấu hình**.

### Cập nhật

Khi có bản mới, trang **Tổng quan** hiện thông báo **"Có phiên bản mới"**: bấm **Cập nhật ngay**, chương trình tự tải bộ cài, cài đè và mở lại. Có thể kiểm tra thủ công bằng **Kiểm tra cập nhật** ở góc dưới thanh bên trái, hoặc tải bộ cài mới từ trang Releases rồi chạy đè lên.

### Gỡ cài đặt

**Settings → Apps → Installed apps → NetWatchdog → Uninstall**. Tác vụ chạy nền được xóa; thư mục `%LOCALAPPDATA%\NetWatchdog` (cấu hình, log) được giữ lại, muốn xóa hẳn thì xóa tay.

### Mở ứng dụng sau khi khởi động lại: khóa màn hình và thời gian chờ

NetWatchdog chạy khi Windows **đăng nhập** xong. Sau khi khởi động lại, Windows 10/11 thường tự đăng nhập lại tài khoản rồi **khóa màn hình ngay**, và các chương trình khởi động cùng máy còn đang chạy lên. Vì vậy NetWatchdog không mở ứng dụng ngay mà làm theo thứ tự:

1. Nếu màn hình đang khóa: báo lên Telegram "🔒 Máy đang khóa…" và **chờ đến khi bạn mở khóa**.
2. Chờ thêm một khoảng cho máy ổn định (mặc định 60 giây; cũng áp dụng khi máy vừa bật dưới 10 phút dù không khóa).
3. Mở các ứng dụng đã chọn và báo kết quả.

Chỉnh ở trang **Khởi động lại & Ứng dụng**: "Chờ mở khóa màn hình" và "Chờ máy ổn định". Lệnh `/apps` trên Telegram mở ứng dụng ngay, không chờ. `/status` có dòng "🔒 Màn hình đang khóa" khi máy đang khóa.

NetWatchdog **không tự mở khóa** được: Windows không cho chương trình nào mở khóa nếu không có mật khẩu. Muốn máy tự vào thẳng màn hình làm việc (không cần ai mở khóa) thì bật tự đăng nhập của Windows:

- Cách 1: dùng [Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) của Microsoft (mật khẩu được lưu mã hóa trên máy).
- Cách 2: `Win+R` → `netplwiz` → bỏ tick *"Users must enter a user name and password…"*. Trên Windows 11, nếu không thấy ô này, hãy tắt *Settings → Accounts → Sign-in options → "For improved security, only allow Windows Hello sign-in…"*.

Lưu ý: bật tự đăng nhập nghĩa là ai bật máy cũng vào được tài khoản của bạn.

## Lệnh Telegram

Bot chỉ nhận lệnh từ đúng Chat ID đã cấu hình. Gửi `/start` hoặc `/menu` để hiện **bàn phím nút bấm** dưới khung chat, bấm thay cho gõ lệnh (`/an_nut` để ẩn). Menu `/` của bot cũng liệt kê sẵn các lệnh.

```
🎮 Bảng JX     📋 JX gọn      📸 Ảnh JX
📊 Theo giờ    📈 Theo ngày   📅 Hôm nay
👤 Nhân vật    🖥 Tình trạng  ❓ Trợ giúp
```

| Lệnh | Tác dụng |
|---|---|
| `/status` | Báo cáo tình trạng máy |
| `/apps` | Mở các ứng dụng đã chọn |
| `/fix` | Chạy quy trình khôi phục mạng ngay |
| `/reboot yes` | Khởi động lại máy sau 30 giây |
| `/cancel` | Hủy lệnh khởi động lại đang chờ |
| `/jx` | Bảng nhân vật jxtdAuto lúc này (khi đã bật theo dõi). `/jx vut`: chỉ nhân vật có tên chứa "vut", không cần gõ dấu. Hiện cả các cột bật thêm trong jxtdAuto: ô trống, vé MT, nhiệm vụ MT, năng động, xu/kim đỉnh, hạn phù |
| `/jx_gon` | jxtdAuto rút gọn, mỗi nhân vật một dòng: tick · tác vụ · EXP/giờ · thu nhập |
| `/jx_homnay` | Tổng kết jxtdAuto trong ngày: EXP/cấp tăng, thu nhập và ngân lượng đầu ngày → hiện tại, số lần chết, số lần đổi tác vụ, thời gian không thấy jxtdAuto |
| `/jx_gio` | EXP và tiền vạn kiếm được **theo giờ** hôm nay. Bên dưới có nút chọn **tất cả** hoặc **từng nhân vật** và nút chuyển sang theo ngày (bấm là sửa ngay tin đó). Gõ `/jx_gio vut, buff` để gộp nhiều nhân vật |
| `/jx_ngay` | EXP và tiền vạn **theo ngày**, 7 ngày gần nhất (`/jx_ngay 30`: 30 ngày), có nút chọn nhân vật như trên |
| `/jx_nv` | Danh sách nhân vật dạng nút bấm: bấm tên để xem chi tiết, kèm nút xem theo giờ / theo ngày của riêng nhân vật đó |
| `/jx_anh` | Ảnh chụp cửa sổ jxtdAuto (chụp thụ động, cửa sổ bị che vẫn chụp được, chỉ không chụp được khi đang thu nhỏ). Xem được cả phần "THÔNG TIN NHIỆM VỤ" của nhân vật đang chọn |

## Theo dõi jxtdAuto

Bật ở trang **jxtdAuto** trong cửa sổ Cài đặt (mặc định tắt). NetWatchdog **chỉ đọc** bảng nhân vật trong cửa sổ jxtdAuto qua lớp trợ năng của Windows (UI Automation): không bấm, không gõ, không đổi cài đặt, không đọc/ghi bộ nhớ của jxtdAuto hay game.

- Mỗi 60 giây đọc đủ các cột: tác vụ, EXP/giờ, thu nhập, ngân lượng, cấp/EXP, phù/chết, thẻ tháng, ô tick, cùng các cột bật thêm trong jxtdAuto (ô trống, vé MT, nhiệm vụ MT, năng động, xu/kim đỉnh, hạn phù). Lịch sử lưu ở `jxtd_history\YYYY-MM-DD.csv` trong thư mục dữ liệu (giữ 90 ngày, mở được bằng Excel).
- Báo cáo định kỳ (mặc định 60 phút), một lần khi khởi động, và theo lệnh `/jx`, `/jx_gon`, `/jx_homnay`, `/jx_gio`, `/jx_ngay`, `/jx_nv`, `/jx_anh` (xem [Lệnh Telegram](#lệnh-telegram)).
- Cảnh báo ngay khi: jxtdAuto bị tắt / không thấy cửa sổ (nhắc lại mỗi 30 phút); nhân vật biến khỏi danh sách hoặc bị bỏ tick; thu nhập chuyển sang âm; **nhân vật đứng chơi** (xem dưới); số lần chết tăng; thẻ tháng còn ≤ 24 giờ và khi hết hẳn; license jxtdAuto còn ≤ 7 ngày.
- **Nhân vật đứng chơi**: chỉ báo khi nhân vật không làm gì, còn đổi nhiệm vụ là bình thường nên không báo. Báo khi cột Tác vụ hiện `Lỗi: …` (vd. "Kiểm tra đầy hành trang") hoặc `Nghỉ … do về thành liên tục` qua 3 lần đọc liên tiếp (lỗi đăng nhập 1–2 phút tự hết thì bỏ qua), hoặc khi nhân vật không có tác vụ / `<Mất kết nối>` / `Treo` / đang đăng nhập kéo dài quá 5 phút. Nhắc lại mỗi 30 phút khi vẫn kẹt, và gửi "🟢 … hoạt động lại" khi làm việc lại. Danh sách từ khóa chỉnh được trong `config.json` (`danger_keywords`, `idle_exact`, `idle_keywords`).
- Mỗi loại cảnh báo của một nhân vật gửi tối đa 1 lần / 30 phút. Hết lỗi thì gửi tin "🟢 … trở lại".
- **Số nhóm** `[0]`, `[1]` trước tên chỉ là số định danh nhóm trong jxtdAuto và có thể đổi: NetWatchdog theo dõi, cảnh báo và thống kê theo **tên nhân vật** phía sau, nên đổi nhóm không bị báo "mất khỏi danh sách" và không tách số liệu.
- Không đọc được phần "THÔNG TIN NHIỆM VỤ": jxtdAuto vẽ thẳng lên tab và chỉ hiện nhân vật đang chọn.
- **Thống kê** (trang **Thống kê jxtdAuto** trong cửa sổ Cài đặt, hoặc `/jx_gio`, `/jx_ngay`): EXP và tiền vạn kiếm được theo từng giờ trong một ngày, hoặc theo từng ngày (7 / 14 / 30 ngày). Chọn tất cả, một hoặc vài nhân vật. Có ô tổng và trung bình mỗi giờ, biểu đồ cột (di chuột để xem số), bảng chi tiết theo thời gian và theo nhân vật (cấp đầu → cuối, số giờ chạy, ngân lượng hiện tại). Tính từ lịch sử CSV ở trên:
  - EXP = chênh lệch cột **EXP tích lũy** giữa hai lần đọc (jxtdAuto 0.4.5v9 trở lên). Dữ liệu cũ chưa có cột đó thì ước tính bằng EXP/giờ × thời gian giữa hai lần đọc. Mốc nào không có số EXP được lưu thì hiện “—”, không phải 0.
  - Các dòng đọc lúc nhân vật mất kết nối (jxtdAuto hiện cấp `0.0%`, EXP/giờ âm) không được tính.
  - Tiền = chênh lệch cột Ngân lượng (quy ra vạn, 1 vạn = 10.000 lượng). Tiêu hay chuyển tiền đi cũng bị trừ vào.
- Kiểm tra nhanh: nút **Đọc ngay** ở trang jxtdAuto, hoặc `python netwatchdog.py --jxtd` (ghi kết quả vào log).

## Cơ chế an toàn

- Chỉ coi là mất mạng sau **N lần kiểm tra lỗi liên tiếp** (mặc định 3 × 30 giây). Có mạng khi kết nối TCP được tới **bất kỳ** đích nào (1.1.1.1:53, 8.8.8.8:53, …) hoặc URL HTTP kiểm tra trả về OK.
- **Giới hạn số lần khởi động lại trong 24 giờ** (mặc định 3) để tránh vòng lặp reboot khi nhà mạng sập lâu.
- **Không khởi động lại khi máy vừa bật** chưa đủ X phút (mặc định 15).
- Đếm ngược trước khi reboot (mặc định 60 giây). Muốn hủy thì gõ `shutdown /a` hoặc gửi `/cancel`.
- Sau một lần khôi phục thất bại mà không reboot, chương trình nghỉ X phút rồi mới thử lại, để không tắt/bật card liên tục.

## Chạy từ mã nguồn / gỡ lỗi

Cần Python 3.10+ và `pip install -r requirements.txt`. Khi chạy từ mã nguồn, cấu hình nằm ngay trong thư mục `netwatchdog/` (đặt biến `NETWATCHDOG_HOME` để đổi chỗ).

- `pythonw app.py`: mở cửa sổ Cài đặt.
- `python app.py --service --dry-run -v`: chạy theo dõi ở console, **chỉ ghi log** (không đổi mạng, không khởi động lại, không mở app).
- `python netwatchdog.py --status`: in báo cáo tình trạng máy. `--test-telegram`: gửi tin thử. `--jxtd`: đọc jxtdAuto một lần.
- Log: `logs\netwatchdog.log`. Trạng thái (hàng đợi tin, lịch sử reboot): `state.json`.
- Test: `python -m unittest discover -s tests`.

## Build & phát hành (CI/CD)

Workflow [`.github/workflows/netwatchdog.yml`](../.github/workflows/netwatchdog.yml) chạy trên GitHub Actions (Windows):

- **Mỗi push / pull request**: chạy test và build bộ cài; file `.exe` tải được ở mục *Artifacts* của lần chạy.
- **Phát hành bản mới**: tạo tag dạng `vX.Y.Z` rồi push, CI sẽ build và tạo GitHub Release kèm `NetWatchdog-Setup-X.Y.Z.exe`. Máy đã cài sẽ tự thấy bản mới.

```bash
git tag v1.0.1
git push origin v1.0.1
```

Build trên máy: cài Inno Setup 6 (`choco install innosetup`) và `pip install pyinstaller`, rồi chạy `powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.1`. Kết quả ở `build\installer\`.

## Cấu trúc

| File | Vai trò |
|---|---|
| `app.py` | Điểm vào của `NetWatchdog.exe`: mở Cài đặt, `--service` chạy nền, `--install-task` / `--uninstall-task` |
| `netwatchdog.py` | Vòng lặp theo dõi, quy trình khôi phục, Telegram (gửi + hàng đợi + lệnh), báo cáo |
| `jxtd.py` | Theo dõi nhân vật jxtdAuto: đọc bảng qua UI Automation, lịch sử CSV, cảnh báo |
| `sysops.py` | Thao tác Windows: `netsh wlan`, `Disable/Enable-NetAdapter`, `shutdown`, mở ứng dụng, Scheduled Task |
| `settings_gui.py` | Cửa sổ Cài đặt (Tkinter), ghi `config.json` |
| `updater.py` | Kiểm tra bản mới trên GitHub Releases, tải và chạy bộ cài |
| `version.py` | Số phiên bản (CI ghi theo tag khi build) |
| `packaging/` | `build.ps1` (PyInstaller + Inno Setup), `installer.iss`, icon |
| `config.example.json` | Cấu hình mẫu. `config.json` thật chứa bot token nên **không commit** (đã có trong `.gitignore`) |
