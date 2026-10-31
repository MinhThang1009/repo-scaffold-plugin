**Ngôn ngữ:** [English](AGENTS.md) | [Tiếng Việt](AGENTS.vi.md)

# Hướng dẫn dành cho người duy trì

## Mục đích repository

Repository này phân phối skill `repo-scaffold` cho Codex và Claude Code. Giữ
skill dùng chung, hai plugin manifest và các scaffold asset được tạo ra tương
thích với hợp đồng host đã công bố.

## Thay đổi và xác minh

- Đọc mã nguồn liên quan và bài kiểm thử tương ứng trước khi sửa. Giữ script
  nguồn, asset được tạo và hợp đồng được tài liệu hóa đồng bộ.
- Chạy riêng `python -m pytest -q` chỉ để phản hồi tập trung/thủ công; dùng test
  runner có kiểm soát để xác minh hành vi cuối cùng.
- Trong lúc triển khai, chạy `python scripts/run_tests.py focused <test-path>`;
  thêm `--source <python-file>` khi kiểm tra coverage cho tệp đó. Sau mọi chỉnh
  sửa, `python scripts/run_tests.py full` đáp ứng yêu cầu pytest đầy đủ và cổng
  coverage trong một lần chạy. Dùng `python scripts/run_tests.py suite` để chỉ
  xác minh runtime. Cả hai lệnh đều gắn với cấu hình pytest chuẩn và kiểm tra
  độ mới của đầu vào. Giữ nguyên đầu vào cho đến khi lượt chạy kết thúc; kết
  quả focused và pytest cache không phải bằng chứng của full suite.
- Chạy `python scripts/validate_repository.py` và
  `python skills/repo-scaffold/scripts/validate_scaffold.py --repository-root . --template-root skills/repo-scaffold/assets`.
- Chạy `python scripts/validate_workflows.py` sau khi sửa workflow, và
  `claude plugin validate --strict .` sau khi sửa plugin manifest hoặc skill.
- Không sửa cấu hình release, cài đặt remote, tag hoặc release trừ khi maintainer
  yêu cầu rõ việc đó.

## Pull request

Trước khi chạy `gh pr create` hoặc `gh pr edit`, chạy
`python scripts/pr_template_preflight.py --title "<title>"`, sau đó dùng template
đã chọn trong repository với `--body-file`. Với template tập trung không bắt
buộc ánh xạ tiêu đề, truyền `--template <id>`. Ghi lại bằng chứng xác minh và
cập nhật bài kiểm thử hồi quy tập trung khi thay đổi hành vi.
Sau khi chuẩn bị tệp body, chạy lại preflight với `--body-file <path>` để từ
chối đoạn văn bị ngắt dòng cứng trước khi sửa GitHub.
