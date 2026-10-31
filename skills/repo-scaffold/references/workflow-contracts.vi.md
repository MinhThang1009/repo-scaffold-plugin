**Ngôn ngữ:** [English](workflow-contracts.md) | [Tiếng Việt](workflow-contracts.vi.md)

# Hợp đồng workflow

Kiểm tra version tool/release dùng identifier số ASCII. SemVer numeric prerelease
identifier không được có số 0 đầu; build metadata có thể có. Ngoại lệ so sánh
link-health phải xác minh cả manifest version dự kiến và tag trước đó trước khi
đọc Git tag hoặc append URL chính xác vào `.lycheeignore`. Giữ rule ignore hiện
có và để ngoại lệ hợp lệ lặp lại có tính idempotent. Version validation pass
không chứng minh quyền release/tag hoặc link remote truy cập được.

Đọc tài liệu này trước khi cài hoặc sửa GitHub Actions workflow.

Code-scanning gate polling phải hữu hạn và có giới hạn trước network access hoặc
sleep: attempts là integer 1–120, delay 0–60 giây. Cấu hình đi kèm 120 lần, mỗi
lần 10 giây vẫn hợp lệ. CLI và từng wait helper xem control sai định dạng là
inconclusive, không phải pass hoặc not-applicable. Các giới hạn này không chứng
minh mọi lần đọc network hay toàn gate kết thúc trong wall-clock deadline; vẫn
giữ request timeout rõ ràng và job limit riêng.

Một lần inspect gate đồng bộ dùng chung budget 500 request, 128 MiB response và
ranh giới thời gian kiểm tra 1.200 giây cho analysis polling, parent/ref read và
alert polling. Lời gọi lồng dùng lại context đó; lời gọi độc lập bỏ context khi
thoát, kể cả exception. Giới hạn byte còn lại được áp trước allocation; retry
được tính kể cả lỗi tạm thời; response đến muộn không được tạo verdict thành
công. Retry sleep phải nằm trong thời gian còn lại. Các giới hạn repository
này giữ cấu hình phát hành nhưng không phải giới hạn GitHub hoặc cơ chế ngắt
cứng HTTP header/body read bị kẹt. Phải xác minh riêng transport/cancellation
trên host hỗ trợ và outer job timeout.

Gate chủ động đóng HTTP error response thuộc sở hữu trước khi giữ phân loại
transient/non-transient. Cleanup failure là inconclusive và không được retry;
không che bằng cách phát lại request. Freshness, official-doc, community-health,
toolchain-registry và action-pin client cũng đóng HTTP error response của chúng
trước khi wrap domain error có sẵn. Cleanup failure vẫn hiện thành domain error,
không phải receipt thành công; network error không qua HTTP không hàm ý có stream
response thuộc sở hữu. Đóng synthetic stream chứng minh resource ownership, không
chứng minh socket mọi platform đã thu hồi hoặc thao tác network bị kẹt đã ngắt.

Từ chối redirect phải đóng response thuộc sở hữu kể cả khi `Location` malformed
trước callback redirect. API/registry client từ chối mọi event 30x được hỗ trợ
trước URL normalization hoặc forwarding. Official-documentation client đóng và
bỏ body redirect mà không đọc vì nó không phải trang cuối; urllib dùng
`Connection: close`. Giữ HTTPS host đã duyệt, native URL normalization và giới
hạn redirect loop. Chỉ đọc body tài liệu cuối theo size cap hiện tại. Cleanup
hoặc redirect malformed phải là domain error có kiểm soát, không phải approval.

Native HTTP response còn `Content-Length` khác 0 sau bounded read là incomplete,
không được parse thành tài liệu đáng tin. HTTP parser failure, gồm chunked body
chưa đủ, vẫn là domain error có kiểm soát. Response fixed-length, chunked và
close-delimited hoàn chỉnh vẫn được hỗ trợ; stream injected không qua HTTP không
được tự suy length. Kiểm tra này xác minh native parser hoàn tất, không bao phủ
mọi framing header sai/nhập nhằng, TLS truncation hoặc hard network deadline
trên mọi host.

Xác minh raw `Content-Length` native trước khi đọc body. Giá trị phải là ASCII
decimal không dấu; giá trị trùng hoặc dạng comma-list phải cùng biểu thị một
canonical length; số 0 đầu không đổi nghĩa. Giới hạn 100 header field/member và
8 KiB text length-field; so canonical decimal size với response cap trước khi
integer conversion. Đây là resource limit của repository, không phải HTTP
platform limit. Native chunked framing vẫn theo precedence transfer-coding;
length field sai vẫn fail closed. Payload fixed-length hoàn chỉnh phải khớp
canonical length kể cả khi native parser không hiểu comma-list hợp lệ giống
nhau.

Chỉ cài workflow cho repository GitHub.com đã xác minh. Mỗi job dùng quyền tối
thiểu; `actions/checkout` có `persist-credentials: false` trừ khi cần giữ
credential; pin external action bằng full SHA đã xác minh và job/service
container image bằng full SHA-256 digest đã xác minh. Giới hạn số lượng/tổng
byte của workflow input được cung cấp. Workflow tạo phải hợp lệ với
`pull_request` (hoặc `pull_request_target` tương đương đáng tin) và
`merge_group` khi check có thể bị bắt buộc. Required-check concurrency dùng
`cancel-in-progress: false`.

Với project GitHub.com có lệnh test/lint chạy được, setup phải kết thúc bằng CI
workflow đã cấu hình hoặc quyết định defer rõ ràng của người dùng. Mọi asset áp
dụng đã duyệt phải qua workflow-installation preflight với đúng `--workflow`
input; mọi companion được tài liệu hóa phải cài và xác minh. Asset tùy chọn có
thể not-applicable nhưng phải ghi lý do, không âm thầm bỏ. Test job generic CI
asset chạy trên `matrix.os`, policy đã review có thể gồm `ubuntu-latest`,
`windows-latest`, `macos-latest`.

Workflow reference `docker://` bên ngoài cần full SHA-256 digest; workflow-
installation preflight từ chối container tag mutable. Docker container action
cần external-action approval; selected policy fail closed trừ khi reference có
thể biểu diễn chính xác và được cho phép. Selected-actions preflight phải theo
`selected_actions_url` đã GitHub xác minh để không thay organization/enterprise
override bằng endpoint repository dễ dãi hơn.

Asset khai báo `pull_request_target` cũng cần review riêng Actions
workflow-execution policy áp dụng. Policy mặc định GitHub cho repository công
khai dự kiến chặn event đó vào 2-12-2026 trừ khi policy phù hợp cho phép tường
minh. Workflow-installation preflight đọc policy Actions kế thừa và fail closed
nếu không có event rule đang bật cho phép trên mọi workflow path được cung cấp.
Khi preflight báo không có bằng chứng, xem [hướng dẫn chính thức](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target);
selected-actions approval không chứng minh event-policy eligibility, và plugin
không đổi remote Actions policy.

Bind workflow-installation inspection với numeric repository ID giữ từ discovery
được duyệt bằng `--expected-repository-id`. `repository_id` kiểu dữ liệu đúng
phải khớp tại consumer boundary. Inspection chưa bind không cho phép copy asset.
Revalidate active state, visibility, Issues, Actions permission, selected policy
URL/content và inherited event policy áp dụng trước khi trả verdict; drift hoặc
final read không khả dụng là inconclusive. Follow selected-policy URL hợp lệ từ
organization/enterprise, nhưng numeric repository-scoped URL không được trỏ
sang ID target khác. Lặp lại đúng inspection trước từng asset copy riêng; các
check này không ngụ ý API/filesystem transaction atomic.

Local reusable-workflow call path phải là canonical repository-relative POSIX
path, không traversal, backslash hay control character. Workflow được gọi phải
được truyền từ cùng workflow directory với caller; basename trùng từ directory
khác là ambiguous. Mỗi workflow được gọi phải khai báo `workflow_call`; từ chối
local call loop. Graph cung cấp không vượt 10 workflow level và 50 workflow con
duy nhất trên mỗi top-level caller. Local action `./...` cũng phải dùng canonical
repository-relative path; từ chối traversal, backslash và control character.

Runtime policy đã review là nguồn sự thật duy nhất. Không lặp supported version
trong prose hoặc workflow YAML; giữ scheduled compatibility canary; nạp
`.github/ci-toolchain.json` qua bundled `ci_toolchain.py run-markdownlint`; giữ
scheduled/manual drift canary; khi policy canary cụ thể phát hiện drift, reconcile
một reminder issue bền vững; không tự cài release chưa review.

Khi CI tải standalone ShellCheck/actionlint archive đã review, cần HTTPS, xác
minh policy digest và retry có giới hạn để lỗi release service tạm thời không
gây gate failure không đáng có. Link checker cũng retry có giới hạn với backoff
cho upstream HTTP error tạm thời nhưng vẫn fail unresolved link.

Action-pin, schema và batch synchronizer chỉ nhận `write` Boolean thật ở library
admission, trước inventory, upstream lookup hoặc config read. `False` là dry-run,
báo path còn pending mà không replace file; `True` chỉ thực hiện replacement
đúng scope đã cho phép. String, number, null và collection là invalid, không phải
giá trị consent thay thế. CLI `--write` vẫn là cờ `store_true` tường minh. Đúng
type không chứng minh user approval hoặc atomicity nhiều file; path/state/readback
theo từng file và mọi downstream publication gate vẫn là yêu cầu riêng.

Snapshot release field từ callback trước khi replace, drift comparison hoặc dùng
lại batch cache. Tag được hỗ trợ phải ASCII, khớp release-tag policy hiện có và
không quá 1.024 ký tự; commit field phải là GitHub SHA-1 ID chữ thường đầy đủ.
Từ chối field thiếu/sai type/invalid trước mọi file replacement. Callback
malformed là inspection failure, không phải drift hợp lệ hay install đang chờ.
Cấu trúc này giữ YAML và canonical pin shape; không chứng minh commit thuộc
upstream đã duyệt hoặc tag/commit receipt mới, đủ và bind đúng.

Native GitHub release resolver bind `ref` của từng reference trả về với chính
xác tên `refs/tags/<tag>` được yêu cầu. Khi peel annotated tag, `sha` của tag
object trả về phải khớp object ID đã hỏi trước khi dùng target. Identity thiếu,
khác hoặc sai type là inconclusive, không được thay bằng release khác. Vẫn hỗ
trợ lightweight commit, nested annotated tag và depth cap hiện có. Các binding
này không chứng minh repository name/tag ổn định trong toàn bộ inspection hoặc
tại write boundary cuối.

Stable action-tag selector không được âm thầm bỏ inventory entry có name thiếu,
rỗng hoặc sai type, hoặc stable tag trùng pattern nhưng commit invalid. Inventory
đó là inconclusive, không chứng minh candidate cũ hơn đang dùng được là mới
nhất. Tag có tên nhưng không stable, như CodeQL bundle/prerelease, nằm ngoài
applicability của selector và có thể bỏ qua mà không xem commit. Validate stable
tag khớp theo bounded ASCII release policy trước khi chuyển numeric component.
Pagination cap và terminal page đầy đủ vẫn bắt buộc; riêng các kiểm tra cấu trúc
này không chứng minh snapshot đồng thời.

Stable tag name lặp chỉ được chấp nhận nếu cùng commit identity. Identity xung
đột trong một page hoặc qua nhiều page là inconclusive, không chọn theo thứ tự.
Receipt lặp giống nhau tự nó không chứng minh inventory đầy đủ/fresh.

Maintenance reader phải giới hạn workflow, release-config và claim-source file
do repository kiểm soát trước decode, rồi fail closed nếu quá lớn hoặc UTF-8
không hợp lệ. Action-pin synchronizer cũng giới hạn inventory workflow 500 file
và tổng 64 MiB để repository lớn không làm cạn maintenance runner khi tạo PR.
Vì manual trigger chọn được branch/tag, checkout phải pin synchronizer về
`${{ github.event.repository.default_branch }}` với
`persist-credentials: false` trước khi chạy repository code; PAT-backed PR
mutation tuyệt đối không chạy code từ ref được chọn. Concurrency group giới hạn
repository và không hủy vì mọi run cập nhật cùng maintenance branch. Static PR
body phải ở `.github/action-pin-sync-pr-body.md`, qua
`python scripts/markdown_body_preflight.py --body-file
.github/action-pin-sync-pr-body.md` và truyền action bằng `body-path`, để đúng
file đã kiểm tra là file gửi GitHub.

Reusable release engine chỉ nhận caller path đã tài liệu hóa. `workflow_dispatch`
trực tiếp phải chọn `release.yml` từ nhánh mặc định; `workflow_call` từ branch
push phải đến từ `release-please.yml` trên nhánh mặc định; tag call phải đến từ
`release-tag.yml` cho `v*` tag vừa push và truyền đúng tag/commit. Build,
attestation, publish job thực thi gate trước mọi write/OIDC permission. GitHub
giữ `github` context caller trong reusable workflow, nên gate bind event/ref/
workflow file với caller path đã duyệt (xem [reusable workflow docs](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)).
Release asset theo tag vẫn cần tag creator đáng tin vì workflow definition lấy
từ commit của tag push.

Giữ các policy outcome thực thi được: `scheduled compatibility canary`,
`do not duplicate supported versions`, `scheduled/manual drift canary`.

- **Documentation:** cài contract với markdownlint và `validate_scaffold.py`,
  runtime từ `ci-toolchain.json`. Copy Markdownlint policy đã review trong
  `scaffold-generation.md` vào root canonical path hoặc giữ/review cấu hình
  project hiệu lực hiện tại. CLI default rules xung đột centered README/template/
  import contract. Chạy linter thật trên target đã render; YAML/template pass
  không chứng minh docs CI đạt. Required `docs-contract` dùng `always()`, trước
  hết xác minh policy preparation thành công và có runtime selector hợp lệ,
  không rỗng. Preparation fail/cancel/skip/indeterminate hoặc thiếu runtime
  output phải fail check trước checkout, dependency install/lint. Không biến
  lỗi preparation thành required check skip vì GitHub có thể coi skip là pass.
- **PR template:** chỉ tin base-SHA tooling trên `pull_request_target`; không
  chạy code PR head; cần đúng một trusted marker và mọi heading/item bắt buộc.
  Dependabot exemption yêu cầu bot user type. Release Please exemption cần
  branch prefix, head repository khớp base và bot user type hoặc account owner
  base (token Release Please có thể mở PR bằng account owner). Cả hai exemption
  chạy Markdown body preflight; branch name riêng lẻ không bao giờ đủ.
- **PR body sync:** chỉ tin base-branch tooling; thu bounded commit/file evidence
  cho exact head SHA; render body summary gọn; giữ live check ở GitHub Checks;
  chạy cả hai body preflight; revalidate PR sát trước `gh pr edit`, verify body
  sau mutation. Không dùng narrative source check-in hoặc chạy PR head code.
  Bind PR number và numeric base/head repo ID ở cả ba API boundary; yêu cầu base
  active, inventory count đúng type trước equality; từ chối duplicate JSON
  member. Name/hash khớp hoặc Boolean/integer equality không cấp quyền write khi
  evidence chưa bind.

  Commit cần prose phong phú có thể dùng `Why:`, `Root cause:`, `Changes:` và
  `Verification:`; field thiếu dùng evidence fallback tường minh, không suy
  claim. Summary nêu kết quả review/cause đã xác nhận, gom key changes thành vài
  chủ đề, để file/check list chi tiết cho GitHub Commits, Files changed, Checks.
  Parse field boundary và multiline value; prose thường không phải structured
  evidence. Giữ revert và fix sau trong scope. Commit `PR-summary-base:` đã
  review có thể gộp phần trước ở đúng base SHA, nhưng commit sau vẫn phải có mặt.
  Từ chối inventory bị cắt/summary quá ngân sách trước update. Gắn verification
  với source revision, giữ risk/acceptance/rollout do tác giả viết. Template/
  Markdown pass không phải verdict đủ nội dung.

Reminder reconciliation xem checker upstream exit status `2` là indeterminate;
phải fail trước Issue mutation và giữ lỗi để review, không coi là stale.

- **Branch protection:** required-check producer phải duy nhất, thực thi được,
  tương thích event, dựa trên regular workflow file. Check Run evidence phải
  resolve về một Actions run có exact producer path, controlling SHA và eligible
  event. Closed-PR no-op dùng check name riêng không required, không báo success
  dưới admission context. Literal closed-state name expression có fallback ổn
  định cho PR mở/merge group; producer name động khác là inconclusive.

  `pull_request_target` chỉ đáp ứng protected default-branch PR coverage khi
  xác minh exact default-branch filter, workflow blob không đổi tại base PR đại
  diện và Actions event policy áp dụng. Merge workflow target mới/sửa trước
  preflight. Job/step `if` hoặc `continue-on-error` có thể skip/che gate phải
  fail closed. Khi effective merge queue áp dụng, cần `merge_group` Check Run
  thành công gần đây từ cùng workflow blob/GitHub App với PR check.

  Trước khi parse YAML remote, xác minh bounded UTF-8 byte chính xác với
  advertised GitHub SHA-1 blob object ID; không normalize line ending/BOM.
  Reusable file cần regular entry trong Git tree commit immutable; Contents API
  có thể dereference symlink, không chứng minh regular file. Byte sai/khác không
  vào workflow-signal cache. Object check không attestation workflow đã chạy
  hoặc external approval.

  Giới hạn root tree entry count trước khi phân loại path. Mọi entry phải là
  object có nonempty path; entry lạ fail closed trước blob read, không loại như
  non-workflow. Giữ exclusion path đã biết ngoài direct-workflow scope. Reusable
  workflow resolution giữ full literal commit pin trước cache reuse/downstream
  inspection. Returned ID khác target là inconclusive dù hash byte đúng.
  Symbolic ref giữ lookup behavior hỗ trợ; không phải runtime-source attestation.

- **Links, community-health và freshness:** network/upstream check chỉ advisory.
  Reminder workflow chỉ chạy từ scheduled/manual event đáng tin; schedule dùng
  POSIX cron năm trường, timezone IANA hợp lệ tùy chọn và manual trigger shape
  hợp lệ. `workflow_dispatch` rỗng được chấp nhận; nếu có input thì tối đa 25
  named mapping với field/type hỗ trợ. Duy trì một issue idempotent khi bật
  Issues. Trước mỗi Issue `create`/`edit` truyền Markdown body file, chạy
  `python scripts/markdown_body_preflight.py --body-file <same-path>`; checker
  từ chối hard-wrap thì dừng trước GitHub mutation.

  Reminder job/reconciliation step không dùng `if`, `needs`, `strategy`,
  `continue-on-error`, `environment`, `timeout-minutes`, `background`,
  `parallel`, `wait`, `wait-all`, `cancel` nếu chúng có thể skip/che preflight;
  policy-drift job được giữ schedule condition, dependency và concurrency bắt
  buộc. Reconciliation shell giữ `errexit`, `nounset`, `pipefail`, không tắt
  trước/sau body preflight. Serialize trạng thái dùng chung của từng reminder
  bằng repository-scoped concurrency không hủy.

  Vì `workflow_dispatch` chọn được branch/tag, reminder manual ghi Issue phải
  checkout `${{ github.event.repository.default_branch }}` với
  `persist-credentials: false` trước repository code, để script/tracker từ nhánh
  mặc định đáng tin. Checkout không pin workflow definition: GitHub [dùng phiên
  bản workflow theo ref đã chọn](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows).
  Do đó workflow manual có write token chỉ được trusted dispatcher và trusted
  workflow ref chạy. Checkout default branch bind script/tệp checkout, nhưng
  không cách ly secret khỏi workflow definition không đáng tin tại ref được
  chọn. Nếu target có writer mức tin cậy thấp hơn, đừng cài/dispatch workflow
  cấp write token hoặc repository secret cho các ref đó.

  Preflight đánh giá effective `issues: write` của từng job; checkout ở job khác
  không thỏa điều kiện. Job-level reusable workflow bị từ chối; local action
  cần trusted checkout chạy trước execution. Community-health tracker path phải
  nằm trong repository và từ chối traversal, control character, link/reparse
  point trước khi đọc. Directory inventory giới hạn 10.000 entry.

  Community-health checker chỉ nhận exit status 0, 1, 2; status khác fail closed
  trước clean/stale branch hoặc Issue mutation. Reminder dùng prefix ổn định,
  khác nhau theo repository:
  `repo-scaffold-community-health-${{ github.repository }}`,
  `repo-scaffold-official-docs-${{ github.repository }}`,
  `repo-scaffold-freshness-${{ github.repository }}` và
  `repo-scaffold-ci-policy-drift-${{ github.repository }}`. Manual branch workflow
  bị đổi tên vì vậy không race với scheduled run.

  Freshness tracker registry v1 chỉ nhận known top-level field và chính xác
  requirement-source field `path`/`locks`; lock path không trỏ tới
  requirement-source. Field lạ fail closed, không bị bỏ qua. Checker giới hạn
  mỗi tracked workflow 5 MiB, file quá cap tạo check indeterminate. Nó resolve
  upstream input độc lập bằng bounded worker pool, giữ finding deterministic và
  fail-closed error; inventory tối đa 500 workflow file/64 MiB, tối đa 500 action
  repository khác nhau. Tracker registry tối đa 500 input path (gồm requirement
  lock); mỗi requirements file tối đa 512 unique direct pin; mọi requirement
  source/lock cộng lại tối đa 4096 pin. Requirements và tracked JSON policy input
  giới hạn 1 MiB trước parse.

  Mọi reminder mutation bind repository tường minh; freshness `create`/`edit`
  cần durable `--body-file` (create cần `--title` không rỗng). Reconciliation job
  có effective `issues: write` và contents read-only, tên `freshness-audit`,
  timeout 15 phút; job-level permission nếu khai báo phải giữ `contents: read`,
  `issues: write`. Freshness API lookup/Issue mutation bind trực tiếp
  `$GITHUB_REPOSITORY`; `gh issue --repo` nêu `github.com/` tường minh. Hardcode
  repository và override biến bị fail closed.

  Lookup dùng GitHub Issue Search API làm GET có giới hạn để tìm Issue đang mở,
  với `is:issue`, `in:body`, freshness marker và `per_page=2`; tối đa trả hai
  issue number đầu để rerun idempotent mà không pagination không giới hạn.
  Trước projection, shipped query yêu cầu `incomplete_results: false` Boolean
  tường minh, `total_count` integer không âm và đúng `min(total_count, 2)` item,
  mỗi item có issue number integer dương, duy nhất. Kết quả
  `[.items[].number] | join(" ")` là một dòng an toàn cho shell; 0 match đã xác
  minh là literal `none`. Empty stdout phải fail trước mutation dù `gh api` trả
  thành công nhưng không có JSON body. Giữ empty-output failure guard canonical
  ngay sau lookup, trước array initialization. Chỉ skip collection khi là
  `none`; không dùng sentinel làm Issue argument. Evidence partial/thiếu/sai
  dạng/không nhất quán phải fail trước mutation.

  Projection theo dòng đã kiểm tra dùng `mapfile` collector khớp; không dùng
  `read` để âm thầm bỏ kết quả thứ hai. Chỉ có một lookup invocation, không thêm
  `gh api` argument; projection vô điều kiện fail validation. Standalone reminder
  installation cũng thực thi check này khi không có code-scanning gate. Lookup
  nằm trong cùng reconciliation path không điều kiện; không seed/tái dùng issue
  output hoặc number state trước đó. Freshness workflow được khai báo nhưng
  lifecycle inspection thực thi bị fail là invalid, không phải optional
  companion vắng mặt, nên installation authorization phải fail. Lookup result
  phải chảy vào Issue number của `close`/`edit`, trực tiếp hoặc qua array; chỉ
  log/kiểm tra kết quả không đủ.

  Reconciliation shell bắt đầu bằng `set -euo pipefail`, không tắt option. Job và
  step không được dùng `if`, `needs`, `strategy`, `environment`, `concurrency`,
  `snapshot`, `cache-mode`, `continue-on-error`; step cũng không dùng
  `background`, `parallel`, `wait`, `wait-all`, `cancel`, `timeout-minutes` có
  thể skip/nhân đôi/che reminder. Checker exit status điều khiển clean/stale:
  clean thì close issue đang có; stale thì edit/create report trước khi fail.
  `CHECKER_EXIT` bind với audit step `checker_exit` output; audit output lấy từ
  checker status. Audit tắt `errexit` khi chạy checker, bắt status rồi khôi phục
  trước khi publish output. Audit chỉ dùng canonical `printf` cho fallback
  report và checker status; output tùy ý/file redirect fail closed.

  JSON/Markdown checker output phải chính xác `$RUNNER_TEMP/freshness.json` và
  `$RUNNER_TEMP/freshness.md`. Reconciliation xác minh cùng marker bằng `grep`
  trước khi rẽ nhánh hoặc sửa Issue; fail nếu nhiều marker issue đang mở. `close`
  hoặc `edit` dùng lookup result không đổi hoặc array element đầu tiên; không shell
  default/parameter transformation. `GITHUB_TOKEN` của audit và `GH_TOKEN` của
  reconciliation đều bind `${{ github.token }}`; không override runner output hay
  report path bằng workflow hoặc shell assignment/parameter expansion.

  Không truyền `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` cho checker. Từ chối
  shell assignment đến process/GitHub CLI config như `GIT_SSH_COMMAND`,
  `GH_CONFIG_DIR`, `HOME`, `LD_PRELOAD`; không cấp env variable workflow/job/step
  nào khác. GitHub expression bị từ chối trong freshness `run`; chỉ exact YAML
  token binding và repository-scoped concurrency expression được phép. Token
  bind không được tham chiếu trong `run`; `gh` chỉ nhận qua YAML binding chính
  xác.

  Reminder job chạy `ubuntu-latest`, effective shell Bash; từ chối shell/runner
  override khác, workflow/job container và service. Checkout/Python setup đã
  review giữ canonical full SHA cùng input, default-branch ref chính xác,
  `persist-credentials: false`, `python-version: 3.x`. Hai preparation action
  xuất hiện đúng một lần, trước đúng ba canonical run step (audit, summary,
  reconciliation). Auxiliary direct job chỉ là mapping inert `steps: []`, không
  execution config. Mọi repository/path/token/cache/input override đều fail
  closed; manual Issue-writing workflow không được ref khác hoặc thiếu ref.

  Job summary chỉ publish report Markdown đã kiểm tra bằng
  `cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"`. Trong
  reconciliation, audit hoàn tất trước lookup, lookup trước mutation. Cấm
  pipeline/background/short-circuit (`|`, `&`, `|&`, `&&`, `||`) quanh các pha;
  cấm shell `!` trong freshness command. Giữ filename canonical `freshness.yml`,
  audit repository-root/JSON-output/Markdown-output arg; audit chạy từ checkout
  root với `--repository-root .`; nếu override `--tracker-registry`, phải là
  `.github/freshness-trackers.json`. Cấm directory-changing command và
  workflow/job/step `working-directory` override. Cấm job-level reusable
  workflow để mọi lệnh/mutation được inspect trực tiếp.

  Freshness chỉ cho phép canonical command set, kể cả các `printf` đã review;
  executable, interpreter, command substitution hoặc path-qualified program
  chưa review phải fail closed. Parameter expansion chỉ dùng canonical report,
  repository, status, issue number. Issue mutation chỉ dùng `--repo`, `--comment`,
  `--title`, `--body-file` đã review; cấm flag thêm và exit status chưa review.
  `printf` format literal, không shell expansion, `%n` hoặc `-v`. Title/issue
  number state theo khởi tạo canonical, không seed lại/đổi thứ tự. Không đổi
  command lookup qua `PATH`, `BASH_ENV`, `ENV` hoặc shell command hash.

  Freshness registry check-in giữ mọi workflow, release, allowlist và requirement
  input đã phát hành; không xóa category để tắt check. Code-scanning allowlist chỉ
  có top-level `schema-version`/`allowlist`; exception path canonical POSIX không
  traversal/control character, có review date hữu hạn và được freshness theo
  dõi. Không cài code-scanning gate thiếu allowlist companion và freshness
  reminder. Freshness được thêm đúng một literal `--language en` hoặc
  `--language vi` cùng reviewed human-facing mapping trong
  `scaffold-generation.md`; bỏ option thì English. Không đổi JSON/status
  protocol, shell guard, path, token binding hay stable job name; từ chối option
  trùng/không hỗ trợ.

Freshness reminder cũng giữ Release Please schema tracker và CI-toolchain policy
tracker đã phát hành để mọi input workflow được cài có cùng độ phủ reminder.
Audit Markdown output tùy chọn phải được dùng làm body file trong cùng job.

Với workflow `pull_request` khai báo write permission, trước hết xác minh
effective token scope theo event, actor và target đã xác minh dưới policy
repository, organization, enterprise áp dụng. Chỉ truyền
`--confirm-pull-request-write-tokens` sau review và bằng Boolean assertion thật.
Setting **Send write tokens to workflows from pull requests** chỉ áp dụng fork
PR của private repository, không phải mọi PR cùng repository. Không nới
fork-token policy để làm asset pass. Với Dependabot asset cùng repository đã
phát hành, review scope khai báo giới hạn và
[hướng dẫn automation của GitHub](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/automate-dependabot-with-actions).
Không chứng minh được effective permission thì hoãn cài. Flag này là operator
assertion, không tự cấp quyền hoặc chứng minh approval provenance.

- **CI:** tạo hoặc điều chỉnh workflow hợp lệ với stack, có command thật và
  aggregate gate ổn định. Không bắt buộc workflow khi scaffold sentinel còn.
  Dùng một runtime policy máy đọc được và dependency cache phù hợp stack.
- **Release:** tách build, attestation đủ điều kiện và publish thành job riêng.
  Không cấp OIDC hoặc write permission cho bước build project. Không ghép
  release-please với tag dispatcher cạnh tranh.
- Dependency review, CodeQL advanced setup, Scorecard, auto-merge, commitlint,
  stale, labeler và release note tùy chọn cần eligibility, permission và user
  approval đã tài liệu hóa. Bỏ option thay vì cài gate đã biết sẽ fail.

Scorecard scan phải xác minh ref type là branch trước khi so short name với
default branch. Manual dispatch có thể chọn tag; tag cùng tên không phải nhánh
mặc định. Thiếu bằng chứng ref type không được pass guard. Namespace check này
không attestation workflow definition đã chạy hoặc thay policy authorization
repository/token.

Trước khi đặt context làm required, xác nhận producer thật, duy nhất, event
coverage mong muốn (gồm `pull_request_target` đáng tin chỉ trên default branch
đã xác minh), không có Check Run/commit-status collision và đúng GitHub App
identity. Job skip hoặc filename workflow đơn thuần không phải evidence đủ.
