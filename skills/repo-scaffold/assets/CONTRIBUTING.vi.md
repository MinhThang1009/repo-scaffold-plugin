# Đóng góp cho {{REPO_SCAFFOLD_PROJECT_NAME}}

Cảm ơn bạn đã quan tâm đóng góp! Tài liệu này mô tả quy trình làm việc.

## Quy trình (GitHub Flow)

1. Tạo branch từ **{{REPO_SCAFFOLD_DEFAULT_BRANCH}}**: `git checkout -b feat/<short-description>`.
2. Commit theo [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/): `feat(scope): description`.
3. Push branch và mở Pull Request vào **{{REPO_SCAFFOLD_DEFAULT_BRANCH}}**.
{{REPO_SCAFFOLD_CONTRIBUTION_REVIEW_STEP}}

Trước khi tạo hoặc cập nhật pull request, chạy
`python scripts/pr_template_preflight.py --title "<title>"`. Với thay đổi
security, deployment hoặc dependency update cần review chuyên biệt mà title
không có mapping bắt buộc, thêm `--template security`, `--template deployment`
hoặc `--template dependency-update`.

Sau khi chuẩn bị tệp body UTF-8, chạy lại preflight với `--body-file <path>` để chặn prose bị hard-wrap trước khi thay đổi GitHub.

Khi đã cài PR-body sync, ghi các field có nhãn rõ ràng `Why:`, `Root cause:`,
`Changes:` và `Verification:` trong commit body. Viết giá trị bằng ngôn ngữ dự
án, dưới dạng đoạn văn hoặc bullet. Mô tả mọi thay đổi quan trọng, kể cả fix mới
và revert. Với PR dài, commit `PR-summary-base:` đã review có thể tổng hợp toàn
bộ diff tại base SHA hiện tại; các commit sau đó vẫn thuộc phạm vi review. Gắn
verification với revision nguồn và đối chiếu body đã sinh với toàn bộ diff trước
khi yêu cầu review. Renderer phải dừng khi không thể rút gọn mà vẫn giữ đủ ý.
Summary phải bao quát thay đổi hành vi chính và các ranh giới an toàn của PR,
không chỉ có tooling hoặc documentation cho PR-body.

Giữ mỗi đoạn văn và mỗi bullet trong commit body trên một dòng vật lý; không
hard-wrap ở 72 hoặc 80 ký tự. Dùng dòng trống để tách đoạn và field, đồng thời
giữ xuống dòng có chủ đích trong code block. Quy ước này được ưu tiên hơn hướng
dẫn wrap commit body chung. Renderer vẫn đọc được message cũ đã wrap mà không
viết lại các commit đó.

## Kỳ vọng về mã nguồn

- Tuân theo các quy ước hiện có của codebase.
- Chạy linter và test trước khi mở PR.
- Mỗi PR chỉ nên tập trung vào một mục tiêu, vì PR nhỏ dễ review hơn.

## Báo cáo vấn đề

{{REPO_SCAFFOLD_ISSUE_REPORTING_GUIDANCE}}

{{REPO_SCAFFOLD_CODE_OF_CONDUCT_SECTION}}
