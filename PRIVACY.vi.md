**Ngôn ngữ:** [English](PRIVACY.md) | [Tiếng Việt](PRIVACY.vi.md)

# Chính sách quyền riêng tư

Ngày hiệu lực: 13 tháng 8, 2026

> Đây là bản dịch tham khảo không có giá trị ưu tiên. Nếu có khác biệt, bản
> tiếng Anh là văn bản điều chỉnh.

## Phạm vi

Repo Scaffold là plugin Agent Skills được phân phối dưới dạng mã nguồn cho
Codex và Claude Code. Plugin gồm một skill, các template và script xác minh chạy
cục bộ. Maintainer không vận hành backend, MCP server, dịch vụ phân tích, hệ
thống tài khoản hoặc endpoint telemetry cho plugin này.

## Dữ liệu được xử lý khi sử dụng

Khi bạn yêu cầu Codex hoặc Claude Code dùng plugin, agent host được chọn có thể
đọc tệp trong repository bạn chọn, tạo hoặc cập nhật tệp repository, chạy công
cụ xác minh cục bộ và dùng phiên GitHub CLI đã xác thực của bạn để thao tác trên
GitHub.com mà bạn cho phép. Plugin hướng dẫn host không tiết lộ thông tin xác
thực và phải xác nhận trước khi thực hiện thay đổi hướng ra bên ngoài.

Plugin không gửi nội dung repository đến dịch vụ do maintainer vận hành. Việc
xử lý do Codex, Claude Code, OpenAI, Anthropic, GitHub, GitHub CLI hoặc công cụ
khác thực hiện chịu sự điều chỉnh của điều khoản quyền riêng tư từ nhà cung cấp
đó và cấu hình tài khoản hoặc workspace của bạn.

## Báo cáo và đóng góp

Khi bạn mở issue, gửi pull request hoặc gửi báo cáo lỗ hổng riêng tư, GitHub lưu
trữ thông tin bạn chọn cung cấp. Maintainer chỉ sử dụng thông tin đó để bảo trì,
hỗ trợ và bảo vệ dự án. Đóng góp công khai vẫn nằm trong lịch sử repository;
báo cáo bảo mật riêng tư vẫn tuân theo quyền truy cập và chính sách lưu trữ của
GitHub.

Không đưa token, thông tin xác thực, nội dung repository riêng tư hoặc dữ liệu
cá nhân không cần thiết vào báo cáo.

## Lựa chọn của bạn

Bạn kiểm soát repository mà plugin có thể thao tác, có thể review thay đổi cục
bộ trước khi commit, có thể từ chối thao tác GitHub từ xa và có thể gỡ cài đặt
plugin. Bạn có thể sửa hoặc xóa issue hay đóng góp GitHub khi GitHub cho phép.

## Liên hệ

Với câu hỏi về quyền riêng tư, hãy dùng kênh riêng tư hoặc công khai phù hợp với
nội dung, theo hướng dẫn trong [SUPPORT.vi.md](SUPPORT.vi.md) và
[SECURITY.vi.md](SECURITY.vi.md).

Các thay đổi quan trọng của chính sách này được công bố trong repository cùng
ngày hiệu lực đã sửa đổi.
