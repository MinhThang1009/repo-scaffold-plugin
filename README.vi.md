<div align="center">

# repo-scaffold

Plugin Agent Skills dành cho Codex và Claude Code, giúp dựng repository mới
theo chuẩn production của GitHub.com.

[![CI](https://github.com/MinhThang1009/repo-scaffold-plugin/actions/workflows/ci.yml/badge.svg)](https://github.com/MinhThang1009/repo-scaffold-plugin/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

**Ngôn ngữ:** [English](README.md) | [Tiếng Việt](README.vi.md)

## Mục lục

- [1. Công cụ làm gì](#1-công-cụ-làm-gì)
- [2. Yêu cầu](#2-yêu-cầu)
- [3. Lợi ích](#3-lợi-ích)
- [4. Cách sử dụng](#4-cách-sử-dụng)
- [5. Hỗ trợ](#5-hỗ-trợ)
- [6. Cài đặt](#6-cài-đặt)
- [7. Cập nhật](#7-cập-nhật)
- [8. Gỡ cài đặt](#8-gỡ-cài-đặt)
- [9. Cấu trúc](#9-cấu-trúc)
- [10. Xác minh khi phát triển](#10-xác-minh-khi-phát-triển)
- [11. Releases](#11-releases)
- [12. Maintainer và đóng góp](#12-maintainer-và-đóng-góp)
- [13. Giấy phép](#13-giấy-phép)

## 1. Công cụ làm gì

Cung cấp một skill `repo-scaffold` dùng chung mà Codex và Claude Code có thể gọi
khi bạn yêu cầu thiết lập các tệp tiêu chuẩn cho repository mới. Skill này:

- Tạo các tệp sức khỏe cộng đồng và bảo trì repository phù hợp với dự án cùng
  các tính năng repository đã bật: README, CONTRIBUTING, SECURITY, SUPPORT,
  CODE_OF_CONDUCT, LICENSE, CODEOWNERS, issue template, PR template mặc định và
  các template chuyên biệt `feature.md`, `bugfix.md`, `documentation.md`,
  `security.md`, `deployment.md`, `dependency-update.md`, Dependabot, CHANGELOG,
  `.editorconfig`, `.gitignore` và `.gitattributes`.
- Với remote GitHub.com đã xác minh, bổ sung kiểm tra tài liệu có tính xác định,
  nhắc kiểm tra upstream community-health hằng tuần, kiểm tra liên kết trong
  pull request và theo lịch, CI workflow phù hợp với stack được phát hiện, cùng
  release workflow có provenance attestation khi repository đủ điều kiện; ngoài
  ra có các workflow tùy chọn (release-please, thiết lập nâng cao CodeQL do
  repository quản lý, dependency review, Dependabot và auto-merge có điều kiện
  label, commitlint, stale, labeler).
- Cấu hình repository GitHub.com đã xác minh: mô tả repository, branch
  protection kiểu classic và label. Ruleset hiện có của repository hoặc tổ chức
  được kiểm tra như chính sách có hiệu lực nhưng không bị sửa.
- Tạo nội dung bằng tiếng Anh, tiếng Việt hoặc cả hai. Ở chế độ song ngữ, người
  dùng cũng chọn ngôn ngữ chính cho các tệp GitHub canonical; tài liệu dịch là
  các tệp sidecar có liên kết và issue/PR template có sẵn ở cả hai ngôn ngữ. Văn
  bản pháp lý upstream canonical tuân theo quy tắc ngôn ngữ đã xác minh của
  nguồn.
- Tạo một điểm vào hướng dẫn `AGENTS.md` dùng chung cho các agent được hỗ trợ
  và một adapter `CLAUDE.md` tối giản để import tệp đó, nhờ vậy Claude Code và
  agent đọc `AGENTS.md` dùng chung hướng dẫn dự án mà không phải sao chép quy
  tắc.

Nội dung tuân theo định dạng community standards của GitHub và lấy từ nguồn
canonical khi có thể (`LICENSE` và `.gitignore` qua GitHub API, Code of Conduct
từ repository Contributor Covenant chính thức); nội dung riêng của dự án được
tạo dựa trên chính repository. GitHub Action bên ngoài được pin bằng commit SHA
bất biến. Dependabot giữ workflow đã cài luôn cập nhật; bộ đồng bộ chỉ tạo PR
hằng tuần để đưa các release đã review vào workflow asset của scaffold. Một
freshness audit theo lịch so sánh action pin, schema Release Please và Python
package pin với release PyPI ổn định mới nhất phù hợp marker của package cùng
runtime matrix đã khai báo; audit cũng kiểm tra ngoại lệ allowlist code-scanning
có thời hạn dựa trên nguồn có thẩm quyền hoặc đầu vào review đã ghi nhận. Sau
đó, audit duy trì một issue nhắc việc cho đến khi xử lý hết drift.

Một quy trình review tài liệu chính thức độc lập chạy hằng tuần để xác minh các
trang nguồn GitHub, OpenAI và Claude Code trong allowlist, claim marker và
khoảng thời gian review ghi trong từng tài liệu plugin liên quan. Quy trình mở
một issue nhắc việc để con người review, không tự sửa nội dung.

Workflow đã phát hành không giao việc thực thi cho container tag có thể thay
đổi. CodeQL và Scorecard cũng hỗ trợ `workflow_dispatch` để chạy quét bảo mật
theo yêu cầu; chạy Scorecard từ nhánh mặc định vì công cụ đánh giá chính sách
repository trên nhánh đó.

Repository công khai cài asset dùng `pull_request_target` cũng cần chính sách
thực thi workflow GitHub Actions phù hợp, cho phép rõ event đó. Chính sách mặc
định cho repository công khai của GitHub dự kiến chặn `pull_request_target` từ
ngày 2 tháng 12 năm 2026; kết quả selected-actions của workflow-installation
preflight không chứng minh event được phép chạy. Workflow-installation preflight
đã đọc các chính sách Actions được kế thừa và từ chối thay đổi nếu không có quy
tắc event đang hoạt động cho phép event trên mọi workflow path được cung cấp.
Hãy xem [hướng dẫn chính sách chính thức](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)
trước khi cài các asset đó. Plugin không thay đổi chính sách remote này.

## 2. Yêu cầu

- [`gh`](https://cli.github.com/) (GitHub CLI), đã xác thực với GitHub.com
  (`gh auth status --active --hostname github.com`), dùng để tìm và cấu hình
  repository GitHub cũng như thực hiện các thay đổi remote mà người dùng cho
  phép.
- `git`.
- Cần `actionlint` và ShellCheck để xác minh workflow cục bộ. CI lấy phiên bản
  đã review, metadata release, cấu trúc archive và digest asset từ [chính sách
  toolchain CI tập trung](.github/ci-toolchain.json).
- Dùng bản phát hành tính năng CPython được khai báo trong [chính sách hỗ trợ
  Python tập trung](.github/python-support.json), cùng development dependency
  được khóa bằng hash, để chạy test xác định, branch coverage, xác minh scaffold
  và preflight từ chối an toàn. CodeQL preflight giới hạn workflow input, output
  GitHub CLI, số API call và tổng runtime; đồng thời yêu cầu xác nhận riêng rằng
  không có tiến trình ngoài hoặc gián tiếp tải kết quả CodeQL lên nơi khác.
  Classic branch-protection preflight gắn repository và nhánh mặc định chính
  xác, chứng minh workflow producer từ xa cùng event coverage chính xác, sau đó
  dùng Check Run thành công gần đây trên PR SHA đại diện do GitHub chọn (test
  merge nếu SHA đó có check/status, nếu không thì dùng head). Preflight nối
  Check Run với Actions workflow run tương ứng và xác minh path, commit, event
  hợp lệ trước khi dùng GitHub App ID của run đó; trên SHA đang kiểm soát không
  được có Commit Status trùng tên. Công cụ từ chối target không hoạt động và
  yêu cầu quyền quản trị hiện tại. Merge-settings preflight giữ các phương thức
  mà effective rules yêu cầu, cần xác nhận riêng trước khi tắt phương thức đang
  bật và từ chối asset auto-merge nếu có merge queue hoặc không xác minh được
  cổng status check bắt buộc có hiệu lực. Security-features preflight gắn chính
  xác các tính năng được yêu cầu với repository đang hoạt động và quyền quản
  trị, thực thi điều kiện secret-scanning trước push protection, chỉ cho phép
  private vulnerability reporting ở repository công khai (kể cả fork), đồng
  thời chứng minh Dependabot alerts đang bật trước khi bật automated security
  fixes, trừ khi đã có chấp thuận bật alerts trước đó. Workflow-installation
  preflight đối chiếu từng tham chiếu `uses:` đã pin SHA với selected-actions
  policy có hiệu lực trước khi cho phép cài workflow bị giới hạn. Nếu không có
  bằng chứng phù hợp, plugin bỏ qua thay đổi đó và báo khoảng trống xác minh.
- Khi áp dụng effective merge queue, branch-protection preflight cũng yêu cầu
  Check Run thành công gần đây trên `merge_group` SHA đã xác minh, do cùng
  workflow blob và GitHub App với PR check được chọn tạo ra; nếu không, quá
  trình thiết lập dừng lại.
- Repository-settings preflight gắn độc lập mô tả/topics, Issues/Discussions
  và việc tạo label với đúng repository GitHub.com; từ chối target đã archive
  hoặc bị vô hiệu hóa; yêu cầu quyền quản trị hiện tại trước khi chạy
  `gh repo edit` hoặc `gh label create`.
- Chỉ cần Node.js 22 trở lên cùng `npx` nếu muốn chạy lại markdownlint được pin
  trong [chính sách toolchain CI](.github/ci-toolchain.json).
- Tự động hóa remote chỉ hỗ trợ GitHub.com. Repository GitHub Enterprise Server
  và GHE.com vẫn nhận các community file cục bộ độc lập với host, nhưng bỏ qua
  workflow đi kèm, badge GitHub.com và cấu hình remote.
- Nếu không có remote, plugin vẫn tạo được các tệp cục bộ độc lập với host. Nó
  hoãn workflow, badge và cấu hình GitHub cho đến khi có remote GitHub.com; không
  bao giờ tự tạo remote khi chưa được xác nhận.

## 3. Lợi ích

Mọi repository mới đều cần cùng một bộ boilerplate production đúng định dạng
GitHub. Công cụ tự động hóa việc này theo ngữ cảnh: đọc dự án để điền nội dung
thực tế thay vì sao chép template tĩnh.

## 4. Cách sử dụng

Cài plugin (xem bên dưới), sau đó trong repository bất kỳ hãy yêu cầu agent được
hỗ trợ:

- “scaffold this repo”
- “scaffold this repo in English”
- “scaffold this repo in English and Vietnamese, with English primary”
- “set up the repo to production standard”
- “dựng repo chuẩn GitHub bằng tiếng Việt”
- “dựng repo song ngữ Anh-Việt, lấy tiếng Việt làm ngôn ngữ chính”

Skill tự kích hoạt và lần lượt thực hiện: khảo sát → quyết định → tạo tệp →
workflow → cấu hình GitHub → bàn giao → xác minh. Trước khi tạo, skill yêu cầu
chọn `en`, `vi` hoặc cả hai; chế độ song ngữ cũng yêu cầu chọn rõ ngôn ngữ chính
cho các tệp GitHub canonical. Ngôn ngữ chính quyết định tệp và cấu hình ở
đường dẫn cố định; tài liệu dịch được liên kết trong `docs/locales/<language>/`,
còn issue/PR template có thể chọn riêng theo locale. Yêu cầu ngôn ngữ không được
hỗ trợ sẽ bị từ chối an toàn. Skill không trộn hai ngôn ngữ trong một tệp,
không ghi đè tệp hiện có khi chưa hỏi và không thực hiện thao tác Git khi chưa
được yêu cầu; các hành động hướng ra bên ngoài cần được xác nhận.

Để biết adapter được hỗ trợ, cách gọi và cách dùng Agent Skills tổng quát, xem
[hướng dẫn tương thích agent](skills/repo-scaffold/references/agent-compatibility.vi.md)
hoặc [bản tiếng Anh](skills/repo-scaffold/references/agent-compatibility.md).

## 5. Hỗ trợ

Xem [SUPPORT.vi.md](SUPPORT.vi.md) để biết hướng dẫn sử dụng và thông tin cần
cung cấp khi gửi báo cáo. Với bản cài đặt cục bộ được chia sẻ, hãy liên hệ người
hoặc workspace đã cung cấp plugin.

Báo cáo lỗ hổng theo [SECURITY.vi.md](SECURITY.vi.md), tuyệt đối không qua issue
công khai. Việc tham gia cộng đồng tuân theo [Quy tắc ứng xử](CODE_OF_CONDUCT.vi.md).
Đọc [Chính sách quyền riêng tư](PRIVACY.vi.md) và [Điều khoản sử dụng](TERMS.vi.md)
trước khi dùng hoặc phân phối plugin.

## 6. Cài đặt

Plugin công khai được cài từ Plugin Directory chung của ChatGPT và Codex.
Repository này chưa tuyên bố có tên trong thư mục công khai. ZIP phát hành đáp
ứng cấu trúc archive cho [luồng gửi chỉ gồm Skills của OpenAI](https://developers.openai.com/plugins/guides/submit-claude-plugin):
có một thư mục gốc `repo-scaffold/`, Claude manifest không rỗng và
`skills/repo-scaffold/SKILL.md` dùng chung. Việc gửi và review vẫn diễn ra trên
cổng OpenAI.

Để cài Codex riêng tư hoặc cục bộ, thêm marketplace của repository này rồi cài
plugin; sau đó mở một Codex thread mới trong repository mục tiêu:

```powershell
codex plugin marketplace add MinhThang1009/repo-scaffold-plugin
codex plugin add repo-scaffold@repo-scaffold-plugins
```

Catalog nằm ở `.agents/plugins/marketplace.json`; đường dẫn nguồn được phân
giải từ gốc marketplace. Marketplace cá nhân tại
`~/.agents/plugins/marketplace.json` vẫn phù hợp khi cần giữ checkout cục bộ
cho một người dùng.

Để cài cục bộ, trỏ marketplace đến thư mục `repo-scaffold/` sạch đã giải nén từ
release ZIP:

```powershell
$pluginRoot = Read-Host "Path to the extracted repo-scaffold directory"
codex plugin marketplace add $pluginRoot
codex plugin add repo-scaffold@repo-scaffold-plugins
```

Không trỏ marketplace cục bộ vào working tree đang phát triển. Codex sao chép
nguồn plugin cục bộ vào plugin cache của từng người dùng (thường là
`~/.codex/plugins/cache`), vì vậy working tree cũng có thể đưa theo build/test
cache bị ignore, metadata `.git` hoặc tệp riêng tư cục bộ. Hãy dùng package
release hoặc archive được tạo từ danh sách đường dẫn release canonical.

Phân phối Claude Code là quy trình riêng. Gửi niêm yết công khai của bên thứ ba
qua [cổng nhà phát triển thư mục](https://code.claude.com/docs/en/plugins/publish)
của Anthropic, cần gói claude.ai trả phí. Niêm yết thư mục có trên claude.ai và
Cowork, sau đó đến Claude Code qua đồng bộ tài khoản. `claude-plugins-official`
là marketplace riêng do Anthropic tuyển chọn. Không tuyên bố plugin đã được
niêm yết trong thư mục Anthropic cho đến khi có thông báo xuất bản. Để cài
Claude Code riêng tư hoặc cục bộ, thêm repository này làm marketplace rồi cài
plugin:

```powershell
claude plugin validate --strict .
claude plugin marketplace add MinhThang1009/repo-scaffold-plugin
claude plugin install repo-scaffold@repo-scaffold-plugins
```

Khởi động lại Claude Code rồi gọi `/repo-scaffold:repo-scaffold`, hoặc yêu cầu
Claude scaffold repository hiện tại. Chỉ để phát triển cục bộ, hãy validate và
nạp checkout trực tiếp bằng `claude --plugin-dir .`. Archive release có cả hai
host manifest và cả hai marketplace catalog. Claude Code v2.1.128 trở lên có
thể nạp ZIP trực tiếp bằng `claude --plugin-dir`; với phiên bản Claude Code cũ
hơn, hãy giải nén trước khi nạp hoặc thêm làm marketplace cục bộ.

## 7. Cập nhật

Trước khi cập nhật, kiểm tra nguồn marketplace đã cấu hình bằng
`codex plugin marketplace list`. Với marketplace dùng Git, làm mới snapshot:

```powershell
codex plugin marketplace upgrade repo-scaffold-plugins
if ($LASTEXITCODE -ne 0) { throw "Marketplace refresh failed; do not reinstall from a stale snapshot." }
```

Với marketplace dùng thư mục cục bộ, bỏ qua `marketplace upgrade`: lệnh đó yêu
cầu nguồn Git. Thay package đã giải nén sạch ở đường dẫn đã đăng ký bằng phiên
bản mới đã review; không sửa tại chỗ phiên bản đã phát hành hoặc trỏ nguồn vào
checkout đang phát triển.

Trước khi cài lại, kiểm tra và giữ nguyên trạng thái bật của plugin. Nếu plugin
Codex đang tắt, đừng tự chạy khối cài lại: `plugin add` có thể bật plugin. Giữ
nó ở trạng thái tắt và báo cập nhật đang hoãn, hoặc xin chấp thuận rõ ràng để
bật lại. Với plugin Codex đang bật:

```powershell
codex plugin add repo-scaffold@repo-scaffold-plugins
if ($LASTEXITCODE -ne 0) { throw "Plugin reinstall failed; do not claim the update succeeded." }
```

Với Claude Code, làm mới listing và cập nhật plugin đã cài riêng; chỉ cập nhật
listing marketplace sẽ không cập nhật phiên bản plugin đang cài:

```powershell
claude plugin marketplace update repo-scaffold-plugins
if ($LASTEXITCODE -ne 0) { throw "Marketplace refresh failed; stop before updating the installed plugin." }
claude plugin update repo-scaffold@repo-scaffold-plugins
if ($LASTEXITCODE -ne 0) { throw "Plugin update failed; retain the old state and report the failure." }
```

Sau khi cập nhật, khởi động lại Codex hoặc Claude Code. Phiên hiện có tiếp tục
dùng phiên bản skill đã nạp lúc phiên bắt đầu.

## 8. Gỡ cài đặt

Gỡ plugin đã cài khỏi marketplace đã cung cấp nó:

```powershell
codex plugin remove repo-scaffold@repo-scaffold-plugins
claude plugin uninstall repo-scaffold@repo-scaffold-plugins
```

Nếu không còn dùng catalog này, hãy gỡ riêng bằng
`codex plugin marketplace remove repo-scaffold-plugins` hoặc
`claude plugin marketplace remove repo-scaffold-plugins`.

## 9. Cấu trúc

Các tệp triển khai và phát hành chính được liệt kê dưới đây. Community policy
và template được lược bỏ cho ngắn gọn.

```text
repo-scaffold/
├── .agents/plugins/
│   └── marketplace.json
├── .coveragerc
├── .release-please-manifest.json
├── .markdownlint-cli2.jsonc
├── .claude-plugin/
│   ├── marketplace.json
│   └── plugin.json
├── .codex-plugin/
│   └── plugin.json
├── .github/
│   ├── CODEOWNERS
│   ├── ci-toolchain.json
│   ├── dependabot.yml
│   ├── labeler.yml
│   ├── official-docs-trackers.json
│   ├── python-support.json
│   ├── release.yml
│   └── workflows/
│       ├── ci.yml
│       ├── codeql.yml
│       ├── commitlint.yml
│       ├── dependency-review.yml
│       ├── freshness.yml
│       ├── labeler.yml
│       ├── links.yml
│       ├── mutation-testing.yml
│       ├── official-docs.yml
│       ├── release-please.yml
│       ├── release.yml
│       ├── scorecard.yml
│       └── stale.yml
├── .editorconfig
├── .gitattributes
├── CHANGELOG.md
├── PLUGIN_SUBMISSION.md
├── PRIVACY.md
├── README.md
├── LICENSE
├── release-please-config.json
├── requirements-dev.in
├── requirements-dev.txt
├── requirements-mutation.in
├── requirements-mutation.txt
├── TERMS.md
├── version.txt
├── scripts/
│   ├── prepare_mutation_cache.py
│   ├── python_support.py
│   ├── run_mutation_testing.py
│   ├── run_tests.py
│   ├── coverage_policy.py
│   ├── audit_freshness.py
│   ├── audit_official_docs.py
│   ├── pr_template_preflight.py
│   ├── sync_action_pins.py
│   ├── sync_versioned_inputs.py
│   ├── validate_mutation_results.py
│   ├── validate_repository.py
│   └── validate_workflows.py
└── skills/
    └── repo-scaffold/
        ├── SKILL.md
        ├── scripts/
        │   ├── audit_freshness.py      # registry-driven stale-input checker
        │   ├── check_community_health.py # versioned upstream drift checker
        │   ├── ci_toolchain.py      # centralized CI bootstrap/tool pin policy
        │   ├── codeql_preflight.py  # fail-closed CodeQL/reusable-workflow inspection
        │   ├── branch_protection_preflight.py # required-check producer and evidence inspection
        │   ├── merge_settings_preflight.py # merge-policy and auto-merge compatibility inspection
        │   ├── repository_settings_preflight.py # metadata and communication mutation eligibility inspection
        │   ├── pr_template_preflight.py # PR template selection and validation
        │   ├── release_preflight.py # release and attestation eligibility inspection
        │   ├── security_features_preflight.py # security-feature mutation eligibility inspection
        │   ├── scorecard_preflight.py # Scorecard SARIF upload eligibility inspection
        │   ├── workflow_installation_preflight.py # GitHub Actions and issue-workflow capability inspection
        │   ├── sync_action_pins.py  # immutable action-release resolver
        │   └── validate_scaffold.py # rendered Markdown and template contract
        ├── references/
        │   ├── readme.md          # README structure guidance
        │   └── github-setup.md    # exact gh configuration commands
        └── assets/                # community-health files + config files (labeler, release configs)
            └── workflows/         # ci, docs, community-health, links, release engine/dispatcher, release-please, CodeQL,
                                   # Scorecard, dependency-review, auto-merge, commitlint, stale, labeler
```

`SKILL.md` → Resources liệt kê các nhóm tài nguyên được phát hành và mục đích
của chúng.

## 10. Xác minh khi phát triển

Để phản hồi cục bộ nhanh, chạy `python scripts/run_tests.py focused <test-path>`.
Thêm `--source <python-file>` để chỉ kiểm tra coverage cho một tệp nguồn `.py`
hoặc `.pyw`. Kết thúc thay đổi bằng `python scripts/run_tests.py full`, lệnh
này chạy toàn bộ test và thực thi coverage floor của repository trong một lần.
Chỉ xác minh runtime thì dùng `python scripts/run_tests.py suite`. Các chế độ
full/suite gắn với cấu hình pytest chuẩn, ghi đè cờ chọn test, từ chối override
discovery/plugin kế thừa và từ chối fingerprint đầu vào đã thay đổi. Lượt đo
dùng dữ liệu mới được cô lập và coverage policy đã đóng băng; filter báo cáo
che tệp chưa được cover sẽ bị từ chối. Mọi chế độ đều báo các pha test chậm.

Lượt kiểm tra có đo lường yêu cầu đủ cả hai thư mục source được khai báo. Báo
cáo full dùng tệp Python trong inventory có giới hạn, gồm cả dotfile; báo cáo
focused nêu rõ từng tệp canonical được chọn thay vì chỉ lọc dữ liệu đã phát
hiện. Tên tệp được chọn từ chối alternate data stream và thành phần phân cách
bằng dấu hai chấm; vẫn hỗ trợ đường dẫn ổ đĩa Windows tuyệt đối và pytest node
ID riêng. Denominator full gồm cả tệp namespace package chưa được import; ghi đè
coverage từ environment, miễn trừ branch và alias nhận diện source không thể
làm yếu cổng. Lựa chọn test/source tường minh phải nằm trong cây repository của
chúng. Kiểm tra freshness gồm source và fixture trong các cây đó, kể cả khi tên
thư mục con trùng tên dependency/cache như `vendor` hoặc `build`.

Lượt đo chỉ chấp nhận bản sao bị lược khỏi inventory sau khi so sánh byte; lựa
chọn focused các bản sao đó sẽ báo kết quả theo source canonical. Cấu hình có
bảo vệ không thể chuyển pytest log/cache hoặc coverage debug output sang đường
dẫn không liên quan. Entry không phải file thường trong cây đầu vào first-party
bị từ chối trước khi thực thi. Tiến trình con được bảo vệ dùng bytecode cache
riêng, mới và được làm mới, giữ nguyên cache hiện có đồng thời ngăn tái sử dụng
mã cũ làm yếu bằng chứng theo source hiện tại. Pytest cache và thư mục temp của
fixture cũng được gắn với runtime root thuộc lượt chạy đó, giữ nguyên trạng
thái dùng chung và bỏ qua chuyển hướng output kế thừa. Xem
[CONTRIBUTING.vi.md](CONTRIBUTING.vi.md) về vòng lặp phản hồi và giới hạn cache.

Plugin không có bước biên dịch. Bài kiểm thử xác minh cần bản CPython trong
[chính sách hỗ trợ Python](.github/python-support.json) và toolchain phát triển
được khóa bằng hash, gồm Coverage.py, markdown-it-py để phân tích CommonMark,
PyYAML và pytest. Chạy các kiểm tra repository từ thư mục gốc:

```powershell
python -m pip install --require-hashes --requirement requirements-dev.txt
python scripts/run_tests.py full
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy --explicit-package-bases skills/repo-scaffold/scripts/check_community_health.py skills/repo-scaffold/scripts/audit_freshness.py skills/repo-scaffold/scripts/branch_protection_preflight.py skills/repo-scaffold/scripts/advanced_codeql_preflight.py skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/dependency_review_preflight.py skills/repo-scaffold/scripts/scorecard_preflight.py skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/pr_template_preflight.py skills/repo-scaffold/scripts/release_preflight.py skills/repo-scaffold/scripts/merge_settings_preflight.py skills/repo-scaffold/scripts/repository_settings_preflight.py skills/repo-scaffold/scripts/security_features_preflight.py skills/repo-scaffold/scripts/workflow_installation_preflight.py skills/repo-scaffold/scripts/sync_action_pins.py skills/repo-scaffold/scripts/validate_scaffold.py skills/repo-scaffold/scripts/markdown_body_preflight.py skills/repo-scaffold/scripts/render_pr_body_evidence.py scripts/audit_freshness.py scripts/audit_official_docs.py scripts/check_code_scanning_alerts.py scripts/coverage_policy.py scripts/merge_mutation_shards.py scripts/pr_template_preflight.py scripts/markdown_body_preflight.py scripts/prepare_mutation_cache.py scripts/python_support.py scripts/run_tests.py scripts/test_shards.py scripts/run_mutation_testing.py scripts/sync_action_pins.py scripts/sync_versioned_inputs.py scripts/update_pr_body.py scripts/render_pr_body_evidence.py scripts/validate_mutation_results.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
python skills/repo-scaffold/scripts/ci_toolchain.py run-markdownlint
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

Trên Windows, CI dùng đường build thuần Python của PyYAML khi cài dependency,
nên ma trận Python được hỗ trợ không cần C compiler cục bộ cho phần mở rộng
libyaml tùy chọn.

Xác minh workflow chạy actionlint, sau đó chạy riêng binary ShellCheck đã review
trên các khối Bash được trích xuất từ workflow đã cài và workflow dạng template.
Markdownlint kiểm tra mọi tệp Markdown do dự án sở hữu.
Coverage đo cả hai cây script first-party với branch coverage và thực thi floor
100% trong `.coveragerc` của CI quality job. Repository validation kiểm tra hợp
đồng README được căn giữa và đánh số, scaffold marker chưa được thay thế,
Markdown issue/PR template, liên kết tương đối, tính duy nhất và cú pháp JSON/
YAML, metadata plugin, issue form, cấu hình Dependabot, cô lập và luồng quyền của
release attestation, cùng cấu trúc chính xác của release archive, gồm mọi runtime
script mà skill tham chiếu. Tag của action đã pin và schema release-please là
thông tin bên ngoài, vì vậy hãy xác minh với repository upstream trong audit
release.

[Chính sách hỗ trợ Python](.github/python-support.json) là nguồn sự thật duy
nhất cho CI. GitHub Actions kiểm thử từng bản phát hành tính năng đã khai báo
trên Ubuntu và các mốc thấp nhất/mới nhất trên Windows cùng macOS. Quality job
dùng giá trị mới nhất trong policy. Canary `3.x` hằng tuần không bắt buộc sẽ
kiểm thử bản Python ổn định mới nhất, sau đó báo lỗi nếu phát hiện drift khỏi
phiên bản đã khai báo để thay đổi hỗ trợ cần được review trong policy. Test job
dùng `matrix.os`, nhờ vậy policy có thể chạy trên mọi hosted platform được khai
báo mà không nhân đôi danh sách runner trong workflow YAML. Repository
validation từ chối drift giữa policy, workflow, scaffold và tài liệu. Identifier
phiên bản policy chỉ dùng ASCII và bị giới hạn 64 ký tự trước khi phân tích số.
Kiểm tra tính liên tục so sánh các phiên bản khai báo liền kề; không được cấp
phát danh sách có kích thước tỷ lệ với endpoint số do policy cung cấp. Các kiểm
tra chấp nhận này không chứng minh tương thích runtime. Canary theo lịch/thủ
công duy trì một issue nhắc việc nếu một trong hai policy đã review cần chú ý.
Quality job cũng chạy format, lint, type, compile, workflow, metadata, link và
kiểm tra release archive.

[Chính sách toolchain CI](.github/ci-toolchain.json) tập trung riêng runtime
bootstrap tài liệu được luân phiên, Python tối thiểu cho tooling đi kèm, npm pin
của markdownlint và metadata/digest release đã review cho công cụ độc lập.
Workflow và hướng dẫn thiết lập dùng policy đó thay vì nhúng các giá trị này;
canary theo lịch/thủ công không bắt buộc báo npm, upstream release hoặc digest
bị drift để review qua cùng issue nhắc việc bền vững. Dependabot kiểm tra hằng
tuần các công cụ phát triển Python đã pin và dependency tài liệu scaffold được
mirror.

`requirements-dev.in` ghi các pin trực tiếp đã review;
`requirements-dev.txt` khóa mọi dependency bắc cầu và ghi SHA-256 PyPI mà CI sử
dụng. Cặp `.in` và `.txt` theo quy ước giúp Dependabot chạy `pip-compile` và cập
nhật cả hai tệp trong một PR. Package có điều kiện theo platform mà ma trận được
hỗ trợ cần được pin trực tiếp để lock tạo trên Linux vẫn cài được bằng hash trên
Windows và macOS. Một bộ đồng bộ bảo trì phiên bản chỉ tạo PR hằng tuần sẽ mở
một draft PR với GitHub Action pin bất biến và schema Release Please được cập
nhật đồng bộ trong workflow, cấu hình và scaffold asset của repository. Bộ đồng
bộ dùng fine-grained PAT riêng lưu trong `VERSION_SYNC_TOKEN` để CI PR thông
thường chạy; nó không tự merge. Chỉ giới hạn token ở repository này, với quyền
**Contents: Read and write**, **Pull requests: Read and write** và **Workflows:
Read and write** vì bộ đồng bộ có thể cập nhật workflow file. Job thủ công từ
chối ref không phải nhánh mặc định và checkout nhánh mặc định với credentials
bị tắt. GitHub vẫn chạy workflow definition thuộc ref `workflow_dispatch` đã
chọn, vì vậy guard này ngăn chọn nhầm ref nhưng không làm workflow definition
không đáng tin trở nên an toàn. PR action nhận PAT giới hạn theo repository,
nên chỉ writer đáng tin mới được dispatch hoặc sửa workflow. Xem
[ngữ nghĩa ref của workflow](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)
của GitHub.

Mọi lượt chạy dùng chung concurrency group không hủy, giới hạn theo repository
vì chúng cập nhật cùng nhánh bảo trì. Giữ token này tách biệt với
`RELEASE_PLEASE_TOKEN` để không cấp quyền ghi workflow không cần thiết cho tự
động hóa release. PR body bảo trì nằm ở `.github/action-pin-sync-pr-body.md`; bộ
đồng bộ preflight chính tệp đó trước khi chuyển cho PR action. Cập nhật Python
được nhóm theo dependency giữa toolchain gốc và
`skills/repo-scaffold/assets/requirements-docs.txt`; cập nhật bảo mật cho hai
package tài liệu được mirror được nhóm riêng.

Workflow [freshness không bắt buộc](.github/workflows/freshness.yml) chạy hằng
tuần, đọc [registry freshness đã review](.github/freshness-trackers.json) và
báo độc lập drift của pin PyPI trực tiếp, tính nhất quán lock cùng đầu vào có
phiên bản mà PR synchronizer chưa cập nhật được. Workflow tuần tự hóa các lượt
chạy theo lịch và thủ công trên mỗi repository, mở hoặc cập nhật một issue
marker khi cần xử lý và chỉ đóng issue sau kết quả theo lịch/thủ công sạch. Có
thể dispatch thủ công từ ref khác trên GitHub, vì vậy reminder luôn checkout và
audit nhánh mặc định trước khi dùng token ghi issue. Scaffold cung cấp cùng
checker theo registry và workflow cho repository được tạo khi có Issues. Chỉ
theo dõi nguồn có owner authoritative và resolver phiên bản xác định. Registry
version 1 từ chối field lạ và đường dẫn lock tới direct source để đầu vào mới
khai báo không bị bỏ qua âm thầm; theo dõi chính sách community-health nằm trong
registry riêng.

Workflow [tài liệu chính thức không bắt buộc](.github/workflows/official-docs.yml)
chạy hằng tuần, dùng [tracker registry tường minh](.github/official-docs-trackers.json)
để xác minh lại URL nguồn có thẩm quyền và claim marker ổn định, rồi yêu cầu
cập nhật ngày trong registry đã review ít nhất mỗi 90 ngày. Reminder theo lịch
và thủ công audit nhánh mặc định kể cả khi GitHub dispatch từ ref khác. Workflow
bao phủ các claim của plugin về Codex, Claude Code, GitHub Actions, Agent Skills,
Conventional Commits và Keep a Changelog. Repository được tạo không kế thừa các
claim chỉ dành riêng cho plugin.

Mutation testing mở rộng toolchain qua `requirements-mutation.txt` riêng, được
xác minh bằng hash. Runner đối chiếu `MUTMUT_VERSION` đã review với pin trực
tiếp; bump Dependabot tương thích phải qua integration test của runner, còn thay
đổi API nội bộ không tương thích phải bị các kiểm tra hành vi phát hiện.
Workflow chạy hằng ngày và dispatch thủ công lập kế hoạch cho mọi mutant trên
Linux, thực thi từng assignment chính xác trong matrix 128 nhánh, và chỉ tái sử
dụng mutation state đã xác minh hash khi source, test, mutation workflow và
dependency khớp trên cùng branch, runtime và platform. Workflow từ chối kết quả
shard thiếu, trùng hoặc chưa đầy đủ trước khi thực thi mutation score floor dựa
trên bằng chứng, được ghi trong [CONTRIBUTING.vi.md](CONTRIBUTING.vi.md). Nó giữ
mutant đã tạo và metadata để chẩn đoán. mutmut không hỗ trợ Windows native;
người đóng góp có thể dùng WSL để chạy kiểm tra tương đương.

Khi không có LibCST wheel tương thích, lock có thể chọn source distribution.
Đường cài đặt đó cần Rust toolchain tương thích và native build tools của
platform trong môi trường cô lập, như mô tả tại
[CONTRIBUTING.vi.md](CONTRIBUTING.vi.md). Metadata đã xác minh bằng hash không
chứng minh native build hoặc mutation run thành công; hãy ghi bằng chứng thực
tế về platform/toolchain hoặc nêu rõ kiểm tra đang được hoãn.

## 11. Releases

Repository dùng Release Please cùng Conventional Commits. Mỗi lần push lên
`main`, công cụ cập nhật pull request phát hành. Khi merge pull request đó,
Release Please cập nhật `CHANGELOG.md`, `version.txt`,
`.codex-plugin/plugin.json` và `.claude-plugin/plugin.json`, tạo tag cùng GitHub
Release ở trạng thái draft, rồi gọi release engine có thể tái sử dụng.

Engine xác minh tag trỏ đến đúng commit, tạo
`repo-scaffold-plugin-<filesystem-safe-tag>.zip` từ commit bất biến, sinh SLSA
build provenance có chữ ký trong job riêng không checkout, đính kèm asset vào
draft và chỉ publish sau khi attestation thành công. Archive có `.agents/`,
`.codex-plugin/`, `.claude-plugin/`, `skills/`, các policy và tài liệu hỗ trợ
được README liên kết, cùng các tệp policy đã review trong thư mục
`repo-scaffold/`. Workflow cần fine-grained PAT lưu trong
`RELEASE_PLEASE_TOKEN`; xem [CONTRIBUTING.vi.md](CONTRIBUTING.vi.md) về quy trình
release và phạm vi token.

Sau khi tải asset, xác minh provenance và workflow signer:

```bash
gh attestation verify repo-scaffold-plugin-vX.Y.Z.zip \
  --repo MinhThang1009/repo-scaffold-plugin \
  --signer-workflow MinhThang1009/repo-scaffold-plugin/.github/workflows/release.yml
```

## 12. Maintainer và đóng góp

Minh Thang duy trì plugin này. Xem [CONTRIBUTING.vi.md](CONTRIBUTING.vi.md) về
quy trình phát triển, kiểm tra bắt buộc và yêu cầu pull request. Vai trò và cách
ra quyết định của dự án được mô tả trong [GOVERNANCE.vi.md](GOVERNANCE.vi.md).
[Hồ sơ gửi plugin](PLUGIN_SUBMISSION.vi.md) ghi lại nội dung niêm yết công khai,
trường hợp kiểm thử và điều kiện tiên quyết khi phát hành bên ngoài.

Khi đề xuất thay đổi, hãy nêu nguồn đã xác minh, các tệp bị ảnh hưởng và lệnh
hoặc kiểm tra thủ công dùng để xác nhận kết quả.

## 13. Giấy phép

MIT. Xem [LICENSE](LICENSE).
