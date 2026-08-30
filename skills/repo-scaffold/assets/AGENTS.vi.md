# Hướng dẫn cho agent

Tệp này là điểm vào hướng dẫn dùng chung cho các coding agent hỗ trợ
`AGENTS.md`.

## Quy ước làm việc

- Tuân theo yêu cầu của người dùng và các hướng dẫn đang áp dụng trong
  repository.
- Đọc các tệp liên quan trước khi thay đổi và giữ nguyên quy ước riêng của dự
  án.
- Giữ thay đổi tập trung, chạy các kiểm tra phù hợp của dự án và báo cáo rõ các
  kiểm tra không thể chạy.
- Không commit, push, tạo pull request hoặc thay đổi cấu hình remote nếu người
  dùng chưa yêu cầu rõ ràng.

## Pull request

Trước khi tạo hoặc cập nhật pull request, đọc tập template tin cậy từ base
branch đích. Khi title Conventional Commit bắt đầu bằng `feat`, `fix` hoặc
`docs`, lần lượt dùng `feature`, `bugfix` hoặc `documentation`. Gate
`pr-template` bắt buộc các mapping này. Với các loại title khác, dùng template
mặc định trừ khi thay đổi thực sự cần review security, deployment hoặc
dependency-update. Giữ đúng một marker `<!-- repo-scaffold:pr-template=<id> -->` phù hợp, mọi heading bắt
buộc và mọi mục của danh sách bắt buộc trong template đã chọn. Chỉ thêm mục
`Khi phù hợp` khi áp dụng, và bỏ cả phần này khi không có mục nào áp dụng. Dùng
tệp body UTF-8. PR ở trạng thái draft có thể để các mục bắt buộc chưa tick;
trước khi chuyển sang ready for review, chỉ tick một mục bắt buộc sau khi đã
hoàn tất. Dùng `gh pr create --body-file` hoặc `gh pr edit --body-file`. Không
dùng `--fill` hoặc `--body` tự do vì có thể bỏ qua template.

## Ngôn ngữ

Dùng ngôn ngữ chủ đạo của tài liệu hướng tới người dùng hiện có. Khi chưa rõ,
hỏi lại trước khi tạo nội dung hướng tới người dùng có độ dài đáng kể.
