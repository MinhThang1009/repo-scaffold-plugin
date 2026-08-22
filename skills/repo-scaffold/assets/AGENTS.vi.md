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

Trước khi tạo hoặc cập nhật pull request, đọc template đang có hiệu lực từ base
branch đích. Giữ nguyên mọi heading và checklist, thay hướng dẫn bằng bằng chứng
cụ thể về thay đổi và verification, sau đó dùng tệp body UTF-8 với
`gh pr create --body-file` hoặc `gh pr edit --body-file`. Không dùng `--fill`
hoặc `--body` tự do vì có thể bỏ qua template.

## Ngôn ngữ

Dùng ngôn ngữ chủ đạo của tài liệu hướng tới người dùng hiện có. Khi chưa rõ,
hỏi lại trước khi tạo nội dung hướng tới người dùng có độ dài đáng kể.
