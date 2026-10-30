**Ngôn ngữ:** [English](scaffold-generation.md) | [Tiếng Việt](scaffold-generation.vi.md)

# Hợp đồng tạo scaffold

Đọc tài liệu này trước khi tạo hoặc cập nhật tệp.

Lấy nội dung canonical của LICENSE và `.gitignore` qua GitHub API. Chỉ thay token
triển khai được giấy phép đã chọn chỉ định rõ; giữ nguyên văn bản pháp lý
canonical và ghi `.gitignore` UTF-8 không BOM. Lấy Contributor Covenant ổn định
mới nhất từ commit immutable của official release branch. Chỉ dùng nguồn tiếng
Việt chính thức nếu tồn tại cho đúng phiên bản đó; nếu không, giữ bản tiếng Anh
canonical và báo khoảng trống.

Upstream discovery receipt phải lấy từ tree đầy đủ, không nhập nhằng, tối đa
100.000 entry và có policy blob regular file tại path đã chọn. Ghi commit và
blob SHA; directory, symlink, path trùng và thiếu object identity đều là
inconclusive. Mỗi version component giới hạn 32 chữ số trước integer conversion.
Kết quả version/current chỉ kiểm tra freshness, không chứng minh body canonical
đầy đủ, enforcement được duyệt hoặc tuân thủ giấy phép; phải review riêng trước
khi cài policy.

Canonical source có thể có website front matter. Chỉ parse và bỏ metadata
envelope đó; xác minh version với immutable receipt đã chọn, giữ policy body và
attribution. Chỉ resolve upstream implementation note về reporting/enforcement
khi đã xác nhận lựa chọn dự án. Không publish website metadata, sao chép note
chưa xử lý, tự tạo cam kết enforcement hoặc tự dịch văn bản pháp lý canonical.
Implementation instruction của MIT license API chỉ định `[year]` và
`[fullname]`; giấy phép khác cần instruction triển khai riêng đã review, không
dùng quy tắc token của MIT.

Sao chép asset và script đi kèm từ skill đúng từng byte khi áp dụng. Mọi workflow
được cài cần script đi kèm, kể cả imported helper, phải có script tương ứng tại
đích root repository:

| Workflow asset | Source đi kèm | Đích được tạo |
| --- | --- | --- |
| `assets/workflows/code-scanning-gate.yml` | `assets/code-scanning-allowlist.json` | `.github/code-scanning-allowlist.json` |
| `assets/workflows/code-scanning-gate.yml` | `../../../scripts/check_code_scanning_alerts.py` | `scripts/check_code_scanning_alerts.py` |
| `assets/workflows/community-health.yml` | `../scripts/check_community_health.py` | `scripts/check_community_health.py` |
| `assets/workflows/community-health.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/community-health.yml` | `assets/community-health-trackers.json` | `.github/community-health-trackers.json` |
| `assets/workflows/documentation.yml` | `../scripts/ci_toolchain.py` | `scripts/ci_toolchain.py` |
| `assets/workflows/documentation.yml` | `assets/ci-toolchain.json` | `.github/ci-toolchain.json` |
| `assets/workflows/documentation.yml` | `../scripts/validate_scaffold.py` | `scripts/validate_scaffold.py` |
| `assets/workflows/documentation.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/documentation.yml` | `assets/requirements-docs.txt` | `requirements-docs.txt` |
| `assets/workflows/documentation.yml` | `assets/markdownlint-cli2.jsonc` | `.markdownlint-cli2.jsonc` |
| `assets/workflows/freshness.yml` | `../scripts/audit_freshness.py` | `scripts/audit_freshness.py` |
| `assets/workflows/freshness.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/freshness.yml` | `assets/freshness-trackers.json` | `.github/freshness-trackers.json` |
| `assets/workflows/freshness.yml` | `assets/requirements-docs.txt` | `requirements-docs.txt` |
| `assets/workflows/freshness.yml` | `../scripts/ci_toolchain.py` | `scripts/ci_toolchain.py` |
| `assets/workflows/freshness.yml` | `assets/ci-toolchain.json` | `.github/ci-toolchain.json` |
| `assets/workflows/freshness.yml` | `../scripts/sync_action_pins.py` | `scripts/sync_action_pins.py` |
| `assets/workflows/pr-body-sync.yml` | `../scripts/render_pr_body_evidence.py` | `scripts/render_pr_body_evidence.py` |
| `assets/workflows/pr-body-sync.yml` | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |
| `assets/workflows/pr-body-sync.yml` | `../scripts/pr_template_preflight.py` | `scripts/pr_template_preflight.py` |
| `assets/workflows/labeler.yml` | `assets/labeler.yml` | `.github/labeler.yml` |
| Pull-request preflight | `../scripts/pr_template_preflight.py` | `scripts/pr_template_preflight.py` |
| Pull-request preflight dependency | `../scripts/markdown_body_preflight.py` | `scripts/markdown_body_preflight.py` |

Workflow installation là quyết định tạo nội dung tường minh. Với dự án GitHub.com
đã xác minh có lệnh test/lint chạy được, phải cài CI workflow đã cấu hình trước
khi tuyên bố scaffold hoàn tất, trừ khi người dùng chủ động hoãn. Với từng asset
được duyệt và áp dụng, chạy read-only `workflow_installation_preflight.py` với
đúng input `--workflow`, copy companion trong bảng và xác minh tệp đã cài. Ghi
mỗi asset tùy chọn là installed, not applicable hoặc deferred tường minh; không
âm thầm bỏ workflow phù hợp hoặc để generic CI sentinel trong dự án đã hoàn tất.

Documentation installation gồm Markdownlint companion đã review tại
`.markdownlint-cli2.jsonc`. Nó giữ lint rule mặc định, cho phép HTML element đã
biết của header căn giữa, semantic line wrapping, link liên hệ thuần và entry
point không phải H1 cho template/import. Không thay policy lint hiện có khi chưa
được duyệt: kiểm tra cấu hình hiệu lực và điều chỉnh output hoặc yêu cầu thay đổi
policy đã review. Policy thiếu/chưa copy là khoảng trống xác minh, không phải
quyền tắt lint. Xóa cả dòng của optional marker bị bỏ và dòng trống cuối dư
trong Markdown được tạo; không sửa literal code example hoặc nội dung dự án đã
viết khi làm vậy.

Asset `pr-body-sync.yml` là workflow metadata PR opt-in. Cài kèm
`scripts/render_pr_body_evidence.py`, `scripts/markdown_body_preflight.py` và
`scripts/pr_template_preflight.py`. Nó render body từ commit PR và field commit
có cấu trúc chính xác tại head SHA hiện tại. Purpose, cause, key change được
gắn nhãn author-reported cùng source commit SHA. Inventory file thay đổi đầy đủ
dùng để xác minh pagination và đối chiếu API count; workflow không đọc patch hoặc
suy luận chủ đề thay đổi. Trạng thái check trực tiếp giữ trong GitHub Checks tab,
không nhúng snapshot cũ vào body. Workflow không dùng narrative body source đã
check-in và không chạy pull-request code.

Copy đầy đủ template catalog bằng ngôn ngữ đã chọn, giữ hướng dẫn về scope và
verification. Đọc `pull-request-contract.md` trước khi chuẩn bị commit field có
cấu trúc hoặc description `PR-summary-base:` tích lũy. Renderer xác minh commit/
file inventory count và fail thay vì âm thầm bỏ chủ đề quan trọng; sau render,
review nội dung với toàn bộ diff.

Khi cài CodeQL hoặc code-scanning gate asset, cũng copy
`assets/code-scanning-allowlist.json` tới
`.github/code-scanning-allowlist.json`. Giữ scaffold allowlist rỗng trừ khi
target repository đã review độc lập một alert cụ thể và ghi alert number,
selector, reason, `reviewed-on`, `review-period-days` chính xác. Không tái sử
dụng exception cho alert mới kể cả tool, rule, path trùng alert cũ. Freshness
reminder mở lại review khi exception đến ngày review; giữ `freshness.yml` nếu cài
gate này. Tracker coi allowlist optional cho đến khi CodeQL hoặc code-scanning
gate asset tạo nó.

Gate poll Pull Request API để lấy test commit mergeable của GitHub trước khi
kiểm tra CodeQL upload. Không thay bằng `merge_commit_sha` trong event payload,
vì giá trị đó có thể chưa có khi GitHub đang tính mergeability.

Trước khi tạo, hỏi: “Bạn muốn dùng ngôn ngữ đầu ra dự án nào: en, vi hay cả hai?
Nếu cả hai, ngôn ngữ nào là chính cho các tệp GitHub canonical?” Chỉ nhận `en`,
`vi`, `both`; khi `both`, yêu cầu chọn rõ ngôn ngữ chính. Gán
`SCAFFOLD_LANGUAGES` theo tập đã xác nhận và `SCAFFOLD_LANGUAGE` theo ngôn ngữ
chính. Với `en`, dùng canonical asset; với `vi`, render sidecar `.vi` phù hợp
thành tên canonical target. Giữ adapter `CLAUDE.md` trung lập ngôn ngữ để import
canonical `AGENTS.md`.

Ở chế độ `both`, không gộp bản dịch trong một file. Dùng ngôn ngữ chính cho
canonical project-authored path và cấu hình chỉ có một vị trí, gồm `AGENTS.md`,
community-health file, `CITATION.cff`, release configuration, workflow message,
label, changelog heading và nội dung commit/PR/release đã được cho phép. Văn bản
pháp lý upstream canonical tuân theo quy tắc source-language riêng. Ngôn ngữ
chính cũng quyết định `.github/ISSUE_TEMPLATE/config.yml` và default
`.github/PULL_REQUEST_TEMPLATE.md`.

Tạo README ngôn ngữ phụ từ cùng dữ kiện dự án đã xác nhận tại
`docs/locales/<secondary-language>/README.md`. Render tài liệu dịch khác từ
reviewed locale asset trong thư mục đó, dùng tên `AGENTS.translation.md`,
`CONTRIBUTING.translation.md`, `SECURITY.translation.md`,
`SUPPORT.translation.md`, `CHANGELOG.translation.md` và
`GOVERNANCE.translation.md` nếu áp dụng. Không dùng active basename đặc biệt như
`AGENTS.md`, `CLAUDE.md`, `SECURITY.md`, `CONTRIBUTING.md` ở đó. Thêm language
link vào root README và mỗi canonical document được tạo có bản dịch. Không tạo
locale copy không hoạt động của `CITATION.cff`, release config hoặc workflow.
Giữ `CODE_OF_CONDUCT` và văn bản upstream canonical khác ở ngôn ngữ source đã
xác minh trừ khi có bản dịch đã review; không tự tạo bản dịch legal policy chưa
chính thức.

Khi chọn cả hai locale, cung cấp cả bản dịch như GitHub template riêng. Đặt
issue form trong `.github/ISSUE_TEMPLATE/` với filename có locale prefix như
`en-bug-report.yml`, `vi-bug-report.yml`, giữ `name` và `description` theo asset
đã localize. Chỉ có đúng một `config.yml`, render bằng ngôn ngữ chính; không copy
`config.vi.yml` thành issue form phụ. Giữ default PR template chính tại
`.github/PULL_REQUEST_TEMPLATE.md`. Đặt cả hai locale của từng focused PR
template và default phụ trong `.github/PULL_REQUEST_TEMPLATE/` với tên có locale
prefix như `en-feature.md`, `vi-feature.md`, `vi-default.md`. GitHub hỗ trợ nhiều
issue template trong `.github/ISSUE_TEMPLATE/` và nhiều PR template trong thư
mục `PULL_REQUEST_TEMPLATE/`; chọn PR variant bằng query parameter `template`.
Không ám chỉ default PR body tự có language chooser.

Với lựa chọn một ngôn ngữ, giữ hành vi hiện tại: render mọi nội dung hướng tới
người dùng do dự án viết bằng `SCAFFOLD_LANGUAGE`, không tạo output ngôn ngữ
khác. Nếu được yêu cầu ngôn ngữ khác, giải thích asset đã review hiện chỉ hỗ trợ
`en` và `vi`; không tự fallback hoặc tạo nội dung đến khi người dùng chọn phương
án hỗ trợ hoặc dừng. Hướng dẫn dự án/tài liệu hiện có có thể giúp đề xuất nhưng
không thay xác nhận. Không suy tiếng Anh từ identifier hoặc literal kỹ thuật.
Dự án đa ngôn ngữ dùng file riêng có link; không để file lai nhiều ngôn ngữ.

Với freshness workflow, giữ English asset khi chọn `en`. Với `vi`, render cùng
canonical `.github/workflows/freshness.yml` qua YAML data, thêm đúng một
argument `--language vi` vào lệnh audit và dùng display string đã review dưới
đây trong title/comment/fallback/`printf` literal tương ứng. Checker localize
heading và prose của report; JSON schema, finding/status value, technical
diagnostic, exit code, marker, path, shell syntax, `%s` placeholder và `\n`
escape không đổi. Không dịch stable job name `freshness-audit` hoặc GitHub
context. CLI chỉ nhận argument tùy chọn `en` hoặc `vi`; bỏ argument thì giữ
English report contract. Language argument không override path, token, tracker
hoặc checker-status binding.

Freshness standalone CLI cấu hình stdout text chuẩn thành UTF-8 để output được
localize không làm đổi indeterminate exit code trên Windows pipe. Library call
giữ stream text do caller truyền vào.

| English display text | Tiếng Việt |
| --- | --- |
| `Repository freshness` | `Freshness của repository` |
| `Checkout` | `Checkout repository` |
| `Set up Python` | `Thiết lập Python` |
| `Audit versioned maintenance inputs` | `Kiểm tra các đầu vào bảo trì theo phiên bản` |
| `Add report to job summary` | `Thêm báo cáo vào job summary` |
| `Reconcile reminder issue` | `Đồng bộ issue nhắc bảo trì` |
| `# Repository freshness report` | `# Báo cáo freshness của repository` |
| `The checker failed before it could produce a report. Inspect this workflow run.` | `Checker đã lỗi trước khi tạo báo cáo. Hãy kiểm tra workflow run này.` |
| `Repository freshness update required` | `Cần cập nhật các đầu vào bảo trì repository` |
| `Found multiple open freshness reminder issues.` | `Có nhiều issue nhắc bảo trì freshness đang mở.` |
| `Freshness checker returned an unexpected exit status: %s` | `Checker freshness trả về exit status không hợp lệ: %s` |
| `Freshness checker was indeterminate; no reminder issue was changed.` | `Checker freshness chưa xác định được kết quả; không thay đổi issue nhắc bảo trì.` |
| `The scheduled freshness audit is clean, so this reminder is closing automatically.` | `Kiểm tra freshness định kỳ không phát hiện đầu vào lỗi thời, nên tự động đóng issue nhắc bảo trì này.` |

Các mapping tiếng Việt sau là đích canonical khi chỉ chọn `vi` hoặc dùng `vi`
làm ngôn ngữ chính. Trong `both`, dùng mapping này cho target chính và path
ngôn ngữ phụ đã mô tả ở trên:

- `AGENTS.vi.md` → `AGENTS.md`
- `CONTRIBUTING.vi.md` → `CONTRIBUTING.md`
- `SECURITY.vi.md` → `SECURITY.md`
- `SUPPORT.vi.md` → `SUPPORT.md`
- `CHANGELOG.vi.md` → `CHANGELOG.md`
- `GOVERNANCE.vi.md` → `GOVERNANCE.md`
- `PULL_REQUEST_TEMPLATE.vi.md` → `.github/PULL_REQUEST_TEMPLATE.md`
- `PULL_REQUEST_TEMPLATE.vi/feature.md` → `.github/PULL_REQUEST_TEMPLATE/feature.md`
- `PULL_REQUEST_TEMPLATE.vi/bugfix.md` → `.github/PULL_REQUEST_TEMPLATE/bugfix.md`
- `PULL_REQUEST_TEMPLATE.vi/documentation.md` → `.github/PULL_REQUEST_TEMPLATE/documentation.md`
- `PULL_REQUEST_TEMPLATE.vi/security.md` → `.github/PULL_REQUEST_TEMPLATE/security.md`
- `PULL_REQUEST_TEMPLATE.vi/deployment.md` → `.github/PULL_REQUEST_TEMPLATE/deployment.md`
- `PULL_REQUEST_TEMPLATE.vi/dependency-update.md` → `.github/PULL_REQUEST_TEMPLATE/dependency-update.md`
- `CITATION.vi.cff` → `CITATION.cff`
- `release-config.vi.yml` → `.github/release.yml`
- `release-please-config.vi.json` → `release-please-config.json`
- `ISSUE_TEMPLATE/bug_report.vi.yml` → `.github/ISSUE_TEMPLATE/bug_report.yml`
- `ISSUE_TEMPLATE/feature_request.vi.yml` → `.github/ISSUE_TEMPLATE/feature_request.yml`
- `ISSUE_TEMPLATE/config.vi.yml` → `.github/ISSUE_TEMPLATE/config.yml`

Chỉ xuất nội dung phụ thuộc capability sau khi capability được xác minh: issue
form và reminder workflow cần Issues; link Discussions cần Discussions; badge
GitHub.com cần repository GitHub.com đã xác minh cùng workflow/license đã cài.
Bỏ section phụ thuộc thay vì tạo dead link.

Chỉ thay marker `{{REPO_SCAFFOLD_*}}` đã tài liệu hóa trong đúng context. Ghi
nhận chính xác marker trong từng source trước render, sau đó chỉ scan output đã
render. Không quét generic double brace vì tài liệu dự án có thể dùng template
expression hợp lệ. Parse mọi YAML/JSON đã render; tạo YAML data bằng serializer,
không nối chuỗi; escape Markdown display text hoặc URL component tại đúng sink.

Xác minh Issue Form đã render như YAML data có type, không coi scalar text làm
mất khác biệt giữa string có quote với Boolean/integer. GitHub cho phép bỏ input
`id`; nếu có thì giữ identifier hợp lệ và duy nhất. Dropdown cần lựa chọn khác
nhau, `multiple` đúng type và `default` là integer index hợp lệ; không gắn default
với lựa chọn `None` hoặc `n/a`. Text `min_length` là integer không âm;
checkbox/validation `required` là Boolean; upload `accept` là chuỗi extension
phân tách bằng dấu phẩy.

Field cấp cao nhất bắt buộc bị thiếu thì phát diagnostic thay vì exception;
attribute/validation không biết theo type, key trùng, giá trị malformed và path
không an toàn đều phải bị từ chối. Schema check cục bộ không chứng minh label có
tồn tại, quyền submit, GitHub feature hoặc render remote; các mục đó vẫn có
applicability check riêng. Form name phải dài hơn ba ký tự mới hiện trong
GitHub chooser và duy nhất giữa YAML/Markdown issue template cùng thư mục. Tên
template có sẵn cũng được tính; không ghi đè hoặc âm thầm đổi tên nội dung dự án
để pass. `title` cấp cao nhất nếu có phải là string; `labels`, `assignees`,
`projects` nhận string array hoặc chuỗi phân tách dấu phẩy, không coercion. Giữ
field bị lược và lựa chọn rỗng hợp lệ, không tự tạo default. Schema pass không
chứng minh label/account/project được gán tồn tại, organization issue type khả
dụng hoặc submitter có quyền ghi project.

Encode giá trị theo đích sử dụng, không blind text replacement. Chỉ dùng
`{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}` trong workflow branch
filter. Trong đúng branch name, thay mỗi `${{` bằng `[$*]{{` sau khi escape `!`
thành `\\!` và `+` thành `\\+`. JSON-encode pattern kết quả rồi bỏ riêng dấu
quote bao quanh JSON string. Cách này ngăn branch hợp lệ như `${{true}}` mở
GitHub Actions expression mà vẫn giữ nghĩa literal branch filter. Chỉ dùng
`{{REPO_SCAFFOLD_DEFAULT_BRANCH}}` cho Markdown display text thuần, với HTML/
Markdown escaping phù hợp sink.

Với Dependabot, giữ update `pip` cố định ở root cho `requirements-docs.txt` và
update `github-actions` cố định ở root với `patterns: ["*"]`. Chỉ tạo thêm
package update từ first-party manifest đã xác nhận; dùng đúng ecosystem
identifier GitHub và repository-relative directory; mỗi block sinh ra phải có
`commit-message.prefix: "chore(deps)"`. Không tạo duplicate root `pip` block.
Không suy ecosystem chỉ từ ngôn ngữ lập trình, giữ cả hai block cố định khi
không có application location được hỗ trợ khác.

Trước khi sửa `pull-request-title-pattern` trong release-please config hiện có,
cập nhật title của từng release PR hiện có sang ngôn ngữ/template đã chọn trước
khi xem PR đó sẵn sàng review.
