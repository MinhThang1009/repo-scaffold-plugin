**Ngôn ngữ:** [English](SECURITY.md) | [Tiếng Việt](SECURITY.vi.md)

# Chính sách bảo mật

## Phiên bản được hỗ trợ

Bản phát hành plugin mới nhất và phiên bản trên nhánh mặc định hiện tại được
nhận bản sửa bảo mật. Các phiên bản cũ không được hỗ trợ; nếu thực tế cho phép,
hãy tái hiện vấn đề trên phiên bản mới nhất trước khi báo cáo.

## Báo cáo lỗ hổng

Không mở issue hoặc discussion công khai về lỗ hổng nghi ngờ. Không đưa chi tiết
khai thác, token, thông tin xác thực, nội dung repository riêng tư hoặc thông
tin nhạy cảm khác vào log công khai.

Hãy dùng kênh riêng tư khả dụng đầu tiên:

1. Dùng [GitHub Private Vulnerability Reporting][private-report] cho repository
   này.
2. Với bản sao được chia sẻ cục bộ, liên hệ người hoặc workspace đã cung cấp
   plugin bằng chính kênh riêng tư dùng để chia sẻ plugin.
3. Nếu không có kênh nào ở trên, hãy yêu cầu phương thức liên hệ riêng tư mà
   không công khai chi tiết lỗ hổng.

Hãy nêu phiên bản plugin bị ảnh hưởng, điểm vào, điều kiện tiên quyết, các bước
tái hiện, tác động và biện pháp giảm thiểu được đề xuất. Maintainer sẽ xác nhận
báo cáo sớm nhất có thể và cập nhật khi có tiến triển đáng kể.

## Phạm vi

Báo cáo bảo mật có thể liên quan đến plugin manifest, hướng dẫn skill, template,
GitHub Actions workflow, lệnh cấu hình repository, helper preflight CodeQL,
vòng đời cài đặt, xử lý đường dẫn, quyền hạn, xử lý secret và tích hợp bên
ngoài.

Báo cáo về GitHub, Codex, GitHub CLI, Python, PyYAML hoặc dependency khác mà
không bắt nguồn từ plugin này nên được gửi cho dự án upstream tương ứng.

[private-report]: https://github.com/MinhThang1009/repo-scaffold-plugin/security/advisories/new
