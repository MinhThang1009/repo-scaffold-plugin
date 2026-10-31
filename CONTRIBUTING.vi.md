**Ngôn ngữ:** [English](CONTRIBUTING.md) | [Tiếng Việt](CONTRIBUTING.vi.md)

# Đóng góp cho repo-scaffold

Cảm ơn bạn đã giúp cải thiện `repo-scaffold`. Repository này có skill Agent
Skills dùng chung cho Codex và Claude Code, template community-health, GitHub
Actions workflow template (gồm thiết lập CodeQL nâng cao), helper CodeQL
preflight viết bằng Python và test hồi quy.

## Trước khi bắt đầu

- Đọc và tuân theo [Quy tắc ứng xử](CODE_OF_CONDUCT.vi.md).
- Với lỗ hổng bảo mật, làm theo [SECURITY.md](SECURITY.vi.md) thay vì mở issue
  công khai.
- Giữ phạm vi thay đổi tập trung. Không gộp các thay đổi template, workflow và
  parser không liên quan trong cùng một pull request.
- Khi thay đổi phụ thuộc vào hành vi bên ngoài, xác minh yêu cầu hiện tại của
  GitHub, OpenAI hoặc Anthropic theo tài liệu chính thức.

## Báo lỗi hoặc yêu cầu tính năng

Dùng [trình chọn issue template của repository](https://github.com/MinhThang1009/repo-scaffold-plugin/issues/new/choose):

- Báo lỗi cần nêu phiên bản plugin, môi trường Codex hoặc Claude Code, stack của
  repository mục tiêu, bước tái hiện, hành vi mong đợi và hành vi thực tế.
- Yêu cầu tính năng cần giải thích vấn đề, hành vi đề xuất, phương án thay thế
  và mọi giới hạn tương thích với GitHub, Codex hoặc Claude Code.

Không đưa token, thông tin xác thực, nội dung repository riêng tư hoặc thông tin
nhạy cảm khác vào báo cáo.

## Thiết lập môi trường phát triển

Plugin không có bước build. Các kiểm tra phát triển cần:

- Bản phát hành CPython được khai báo trong [`.github/python-support.json`](.github/python-support.json)
- Coverage.py
- markdown-it-py để xác minh Markdown tuân thủ CommonMark
- PyYAML
- pytest
- Git, gồm `hash-object` để tạo object ID độc lập cho fixture
- mutmut trên Linux, macOS hoặc Windows qua WSL
- Ruff
- mypy
- Node.js 22 trở lên cùng `npx` để chạy markdownlint
- actionlint
- ShellCheck
- `pip-tools` chỉ khi tạo lại lockfile; ghi phiên bản đã dùng trong phần xác minh của pull request

Test tích hợp reminder-query gốc cũng dùng GitHub CLI và Bash sẵn có (trên
Windows là Git Bash). Chúng chỉ cung cấp response tổng hợp qua loopback, cô lập
cấu hình GitHub CLI và không cần thông tin xác thực thật hoặc ghi lên GitHub.
Nếu thiếu một trong hai công cụ, báo trạng thái skip do không áp dụng thay vì
tuyên bố test tích hợp đã đạt.

Wrapper preflight được import dùng lại code đã nạp kèm, nhưng vẫn xác minh từng
body mới và đọc lại template target hiện tại. Cơ chế này cache code, không cache
bằng chứng hoặc verdict của target. Helper test parser đi kèm dạng focused cũng
dùng lại một module trong cùng process; trampoline của mutmut vẫn chọn mutant
đang hoạt động ở mỗi lần gọi. Linux integration fixture kiểm tra hành vi đó với
backend thật.

HTTP response regression helper cũng dùng lại tối đa 16 module repository bất
biến, thay vì nạp code đã mở rộng cho từng trường hợp framing hoặc redirect.
Repository target tạm thời không được cache; mỗi response vẫn được đọc và xác
minh độc lập. Helper khôi phục alias module sau khi nạp và giữ namespace
mutation canonical của từng source. Linux fixture riêng chạy helper thực tế
này với mutant thật để kiểm tra verdict được cô lập.

Hook pytest session-finish thu gom các chu trình module tạm thời không còn được
tham chiếu trước khi runner chạy trong process có thể đóng băng chúng vào heap
kế thừa. Hook không mở băng đối tượng còn sống, đổi ngưỡng hoặc trạng thái GC,
sửa exit status, hoặc thay đổi mutation worker, exclusion hay score floor.

Shard wrapper bật diagnostics có giới hạn về lúc submit/hoàn tất runner. Chỉ ghi
mutant key được phân công và PID khi backend đã pin cung cấp được kết quả khớp
không mơ hồ; việc này độc lập với lấy mẫu process title. Identity chưa xác định
không phải bằng chứng cấp quyền hoặc verdict. Mẫu tài nguyên và sự kiện runner
mỗi loại có ngân sách 1 MiB, tổng diagnostics không vượt 2 MiB; hết ngân sách
được báo tường minh. Argument của runner, kết quả trả về, exception và factory
gốc được giữ nguyên. Linux shard fixture thật đối chiếu các sự kiện với
inventory mutant được phân công.

Test repository-validator và workflow-preflight cấp cho bộ phân loại HTTP token
gốc allowance tuyến tính để đọc token, dùng chung giữa các lát và khởi tạo mới
cho mỗi lần gọi. Nếu mutation parser đặt lại index, test phải raise assertion
trước khi danh sách method tăng không giới hạn. Module fixture bao phủ lời gọi
trực tiếp và cấp cao hơn rồi khôi phục function nguồn. Nó không thay verdict
phân loại, không chạy code artifact đã tạo và không đổi giới hạn production,
mutation worker, timeout, exclusion hoặc quality floor.

Text-parser fixture cũng giới hạn sự kiện gọi cho việc gộp shell continuation
và tìm/xóa HTML comment. Cơ chế này bắt các lượt quét nội bộ không tiến triển
và việc cấp phát output lặp lại, nhưng không thay input hoặc kết quả parser.
Mỗi lần gọi có allowance mới tỷ lệ với độ dài văn bản. Callback profile của
thread hiện tại chuyển tiếp profiler trước đó và khôi phục nó ngay cả khi test
assertion hoặc parser exception xảy ra; callback không thay coverage trace hook.
Profiler native không callable bị từ chối mà không bị thay thế. Đây là test
assertion, không phải sandbox hay giới hạn tài nguyên production.

Linux fixture sao chép classifier, comment parser và line parser đang được kiểm
tra từ repository thật sang các repository cô lập, sau đó xác minh mutant thật
reset index, giảm index và bỏ search offset đều bị đánh dấu killed. Việc chọn
fault dựa trên cú pháp, không phụ thuộc mutant ID lịch sử.

Workflow-blob fixture dùng `git hash-object --stdin --no-filters` làm oracle
object-format độc lập. Tối đa 128 fixture ID được memoize theo chính xác nội
dung văn bản; đây là dữ liệu object-format xác định, không phải bằng chứng
GitHub hoặc quyền cấp phép được cache. Workflow text là dữ liệu tổng hợp, không
phải credential hay password hash. Test hợp đồng Windows pipe backend chạy qua
API mô hình hóa trên mọi platform; vẫn giữ riêng test Windows native. Các test
hợp đồng đạt trên Linux không chứng minh ABI Windows hoặc hành vi pipe native.

Quality run của từng platform giữ coverage floor đã khai báo. Không loại backend
native chỉ vì quality runner dùng OS khác; hãy kết hợp contract seam portable
với kiểm tra tích hợp native phù hợp.

CI pin cho markdownlint và công cụ độc lập được tải xuống, cùng runtime bootstrap
tài liệu luân phiên và Python tối thiểu cho tooling đi kèm, được quản lý trong
[`.github/ci-toolchain.json`](.github/ci-toolchain.json). Chỉ sửa policy đó sau
khi review npm hoặc upstream release cùng asset digest nếu có.

Nên dùng PSScriptAnalyzer khi thay đổi thêm PowerShell snippet.

Trên Windows, CI chọn phần mở rộng libyaml tùy chọn của PyYAML theo chế độ
thuần Python khi cài dependency. Cách này giúp build từ source PyYAML được khóa
bằng hash dùng được trên bản Python Windows mới được hỗ trợ mà không cần cài
compiler hoặc đổi dependency pin.

Cài toolchain phát triển đã resolve đầy đủ và xác minh hash từ thư mục gốc
repository:

```powershell
python -m pip install --require-hashes --requirement requirements-dev.txt
```

Khi đổi pin trực tiếp trong `requirements-dev.in`, tạo lại
`requirements-dev.txt` bằng lệnh chính xác ghi trong header lockfile và ghi
`pip-compile --version` vào phần xác minh pull request. Kiểm tra toàn bộ diff
dependency bắc cầu và xác minh lock trên từng hệ điều hành cùng phiên bản Python
trong `.github/python-support.json` trước khi commit. Dependabot dùng cặp `.in`
và `.txt` theo quy ước để tạo lại output khóa bằng hash trong các cập nhật
dependency thông thường. Yêu cầu review và xác minh tương tự vẫn áp dụng cho
diff mà nó tạo. Các cập nhật dùng chung với
`skills/repo-scaffold/assets/requirements-docs.txt` được nhóm tại cả hai vị trí
để scaffold đi kèm không lệch khỏi toolchain repository. Pin không điều kiện
`colorama`, `exceptiongroup` và `tomli` giúp một lock cài được trên các OS và
phiên bản Python được hỗ trợ, kể cả khi Dependabot tạo lại lock trên Linux bằng
interpreter mới hơn. Khai báo tường minh mọi package có điều kiện theo platform
mà support matrix cần trong `requirements-dev.in`, vì `pip-compile` resolve theo
platform của máy đang chạy.

Mutation testing dùng lock riêng vì mutmut cần hỗ trợ `fork` của hệ điều hành.
Sau khi đổi `requirements-mutation.in`, tạo lại `requirements-mutation.txt`
theo cùng quy trình: ghi `pip-compile --version` và giữ nguyên tùy chọn hash
mode trong header lockfile. Pin `toml` không điều kiện giữ dependency Python
3.10 của mutmut khi tạo lock bằng interpreter mới hơn. Tạo mutation lock bằng
CPython 3.13 để giữ dependency `PyYAML-ft` chỉ dành cho Python 3.13 của LibCST
cùng hash tương ứng. Khai báo provider này bằng pin tường minh có marker
`python_version == "3.13"` trong `requirements-mutation.in`; nó dùng namespace
riêng `yaml_ft`, không thay package `yaml` của development toolchain. Repository
validator từ chối nếu thiếu provider hoặc marker làm nó hoạt động trên runtime
khác. Bản cập nhật mutmut phải qua integration và behavioral test của runner;
validator lấy phiên bản đã review từ input trực tiếp thay vì lặp lại con số đó.
Chỉ tạo lại lock là chưa đủ. Entry `also_copy` của mutation workspace phải là
chuỗi path; legacy contract validator từ chối giá trị không phải chuỗi trước khi
kiểm tra membership.

Lượt chạy theo lịch và thủ công đáng tin cậy lập kế hoạch cho mọi mutant, chạy
đúng assignment trong 128 worker Linux và chỉ tổng hợp tập kết quả đầy đủ, không
chồng lấn. Mutation cache đã xác minh hash chỉ được tái sử dụng state khi
fingerprint source, test, mutation workflow, dependency, branch, runtime và
platform khớp. Job plan lưu state đã tạo sau khi ghi hash đầu vào và state. Lượt
chạy shard thất bại có thể dùng lại plan. Verdict đã hoàn tất được lưu sau khi
tổng hợp và qua score gate. Shard artifact chỉ có tệp kết quả `.meta` với path
tương đối trong `mutants/`. Generated source được giữ trong plan artifact dùng
chung, tránh upload lặp lại từ cả 128 shard rồi tải lại ở job tổng hợp.
Aggregate job từ chối artifact bị thiếu, kết quả không được phân công hoặc shard
chưa hoàn tất trước khi xuất thống kê. Khi cache hit đã xác minh, verdict killed
đã giữ lại được gộp và chỉ assignment còn pending mới chạy. Cách này tiếp tục
được toàn bộ mutation run mà không chấp nhận cache một phần hoặc hạ score gate.

Nếu bước chuẩn bị làm mất hiệu lực cặp source/cache metadata, hãy xóa shard plan
hiện có cùng thống kê cũ trước khi báo có thể dùng lại plan. Tạo lại plan đầy
đủ; kết quả killed đã xác minh từ source khác vẫn được tái sử dụng. Tên plan còn
sót không được biến state source chưa đầy đủ thành cache hit.

Merger chấp nhận tối đa 10.000 directory entry trong lượt đi metadata. Nó kiểm
tra ngân sách khi đọc từng iterator, trước khi giữ lại và sắp xếp entry. Kể cả
tệp không liên quan cũng tính vào ngân sách; vượt giới hạn thì đóng iterator và
dừng trước khi publish metadata.

Cache metadata dùng tệp tạm duy nhất thuộc sở hữu của lượt chạy để thay thế.
Nếu publish lỗi, dọn tệp tạm mà không thay destination cũ. Lỗi cleanup được báo
riêng, giữ lại lỗi I/O xảy ra trước đó; lỗi sau khi thay thế không đồng nghĩa
với rollback hoặc destination không đổi.

## Thực hiện thay đổi

1. Tạo branch có phạm vi rõ ràng từ nhánh mặc định.
2. Dùng tiếng Anh cho tài liệu, commit message, metadata issue và pull request,
   release note cùng nội dung hướng tới cộng đồng khác. Tuân theo quy ước code
   hiện có.
3. Dùng Conventional Commit, ví dụ `fix(preflight): bound shell parsing`.
4. Thêm hoặc cập nhật regression test tập trung cho thay đổi hành vi.
5. Cập nhật tài liệu khi yêu cầu, template hoặc hành vi thấy được của người
   dùng thay đổi.

Không làm yếu validation, bỏ qua warning hợp lệ, tắt test hoặc thay kiểm tra
thật bằng kết quả hardcode.

## Xác minh thay đổi

Các job bắt buộc `commitlint` và `code-scanning-gate` vẫn chạy với event `edited`,
kể cả khi sửa metadata sau khi PR đóng. Chỉ PR có state `closed` tường minh mới
được trả kết quả not-applicable mà không xác minh commit hoặc poll analysis. PR
đang mở và merge group giữ đầy đủ gate; state không xác định thì fail. No-op của
PR đóng dùng riêng `commitlint-closed-pr` và `code-scanning-gate-closed-pr`,
không thể tính nhầm vào required context trên cùng head SHA. Context admission
thông thường vẫn là `commitlint` và `code-scanning-gate`. Commitlint checkout
đúng head SHA từ event. Cách xử lý này không sửa check lịch sử đã fail hoặc
chứng nhận PR đóng từng qua validation.

Code-scanning gate yêu cầu `error` và `warning` trong CodeQL analysis mới nhất
của từng commit/category ứng viên phải có mặt và rỗng. Diagnostics thiếu, sai
định dạng hoặc khác rỗng khiến category không khả dụng; upload cũ khỏe mạnh
không che được upload thay thế suy giảm, kể cả khi API phân trang. Quy tắc áp
dụng cho SHA chính xác và event merge commit tương đương; category không khả
dụng fail trước khi alert disposition có thể cho phép ref.

Khi admission PR, gate bind base/head của PR đang mở và parent của test-merge
với event kích hoạt trước khi đọc analysis; xác minh lại sau khi đọc alert và
trước verdict. Trạng thái đã đổi, đã đóng, sai định dạng hoặc không khả dụng
không được tái dùng analysis thành công cũ. Các lần đọc tuần tự không làm trạng
thái GitHub atomic sau lần cuối; cho phép merge vẫn cần check mới trên SHA điều
khiển.

Trong lúc phát triển, chọn test file hoặc pytest node ID liên quan để chạy nhanh.
Runner cục bộ báo 20 pha chậm nhất:

```powershell
python scripts/run_tests.py focused tests/test_pr_template_preflight.py
python scripts/run_tests.py focused tests/test_pr_template_preflight.py --source skills/repo-scaffold/scripts/pr_template_preflight.py
```

Ma trận native warning-budget chạy chồng lấn tối đa hai subprocess case cô lập
để vừa ngân sách CI. Mỗi case giữ target, runner output và timeout riêng; worker
phải kết thúc trước khi thu kết quả/exception theo thứ tự case. Assertion và
unittest subtest vẫn chạy ở thread gọi. Không tái dùng verdict hoặc đổi mutation
worker, lựa chọn test, coverage requirement hay timeout job.

`--source` thực thi coverage floor hiện có cho tệp `.py`/`.pyw` được chọn; lặp
cờ cho tệp khác. Nếu chọn một trong ba bản sao bị lược bỏ đã xác minh, báo cáo
theo script canonical trong `scripts/` có byte giống hệt; chọn cả hai path vẫn
chỉ đo một identity. Mapping dựa trên kiểm tra byte và không che thiếu coverage
canonical ở các tệp khác. Đây chỉ là phản hồi focused. Mỗi lượt đo dùng coverage
data file mới, cô lập và xóa sau đó, tránh các lượt đồng thời ghi đè `.coverage`
hoặc trộn kết quả cũ với code mới.

Khi xong thay đổi, chạy `python scripts/run_tests.py full`: chạy toàn bộ suite một
lần dưới Coverage.py rồi kiểm tra branch floor của `.coveragerc` không đổi.
Chạy pytest full riêng sẽ lặp lại cùng test. CI chạy full suite trên Ubuntu và
runtime biên trên Windows/macOS. Mỗi Windows runtime chia thành hai shard được
bảo vệ: `native-runner` chạy `tests/test_run_tests_native.py`; `remainder` tự
phát hiện mọi test module khác. Hai shard không trùng, module mới tự vào
`remainder` và `ci-success` đợi tất cả shard.

Workflow validation kiểm tra execution control ngoài nội dung command: runtime,
coverage, integration check bắt buộc và bước verdict `ci-success` phải chạy
foreground, không optional và truyền lỗi. Verdict job giữ `always()` để đánh
giá producer fail/skip. Policy preparation cũng yêu cầu execution bắt buộc,
truyền lỗi và bind mỗi command vào step ID dùng cho matrix/latest/toolchain
output. Mỗi runtime, coverage, integration và canary áp dụng cần đúng một bước
`actions/setup-python` đã pin, bind phiên bản policy cùng cache lock. Setup và
hashed dependency install phải bắt buộc, fatal, theo đúng thứ tự trước test gate;
setup phải đứng trước install và cập nhật PATH. Tải Python với
`update-environment: false` không chọn interpreter cho lệnh `python` kế tiếp.
Step setup/install/gate trùng gây nhập nhằng thì admission fail. Guarded install,
bootstrap Windows có điều kiện và applicability của scheduled job được giữ.

Canary áp dụng phải có verdict foreground, bắt buộc, fatal trong workspace/shell
được bảo vệ. Python runtime drift check chạy sau test suite, vốn chạy sau setup
và hashed install. `check-latest` gắn với đúng Python setup đã pin. Mỗi canary
cần verifier duy nhất; check skip hoặc cho qua lỗi không thể làm sạch reminder.
Điều kiện scheduled/manual giữ nguyên; canary không thuộc CI aggregate bắt buộc.

Producer lệnh Python bắt buộc (kể cả policy preparation) bind shell và working
directory hiệu lực theo precedence workflow/job/step. Chỉ shell tích hợp GitHub
được chấp nhận vì template tùy chỉnh có thể bỏ qua script. Directory phải giữ
workspace (`implicit`, `.`, `./` hoặc `${{ github.workspace }}`), không chọn
script tương đối ở checkout khác. Từ chối khai báo ở mọi scope các biến
`BASH_ENV`, `SHELLOPTS`, `PYTHONINSPECT`, `PYTHONPATH`, `PYTHONHOME`,
`PYTHONUSERBASE`, `PYTHONPLATLIBDIR`, `PYTHONPYCACHEPREFIX`: chúng có thể tắt
execution, biến Python exit lỗi thành REPL thành công hoặc nạp startup code
trước runner. `sitecustomize.py` trong `PYTHONPATH` có thể thoát thành công
trước khi runner xác minh input/receipt; xóa biến sau khi interpreter khởi động
không bảo vệ được. User-site và platform-library cũng có thể chọn startup code;
hành vi phụ thuộc host, interpreter và venv, nên một biến không hoạt động ở một
venv không chứng minh an toàn trên CI matrix. Bytecode-cache prefix bên ngoài
cũng có thể thay startup bytecode; từ chối override đã khai báo, nhưng vẫn cho
phép prefix do runner tự tạo và sở hữu.

Mutation validator kiểm tra cùng command context ở mọi script step trong plan,
shard và quality job, kể cả giá trị workflow/job kế thừa. Cấu hình chỉ dành cho
action không phải Python command context. Điều kiện cache reuse, artifact
action, scalar opt-in, số shard và score floor không đổi. Scalar env khác như
lựa chọn PyYAML Windows và integration opt-in vẫn được hỗ trợ. Kiểm tra này xét
context đã khai báo, không phân tích tùy ý hành vi step đáng tin trước đó hoặc
startup state của host.

Verifier `ci-success` dùng grammar Bash có giới hạn, không tìm chuỗi so sánh.
Guard phải OR các so sánh khác success đã quote cho từng result bind, nhánh đó
exit status 1–255, sau đó chỉ được in diagnostics đã quote và tùy chọn exit
thành công cuối cùng. Thứ tự biến, whitespace, diagnostics có thể thay đổi;
header continuation phải theo `||`, không được tách một phép so sánh. Parser
dùng ranh giới dòng/whitespace của Bash, không dùng Unicode line separator.
Comment, early success, AND guard, result thiếu hoặc status 256 không thay được
failure propagation. Shell program không hỗ trợ bị từ chối mà không thực thi;
đây không phải shell analyzer tổng quát. Context verdict phải giữ Bash trên
Ubuntu runner đã review, không có workflow/job env override hay bước chuẩn bị
thêm có thể thay môi trường thực thi.

Bootstrap platform có điều kiện và scheduled canary chỉ áp dụng khi phù hợp;
chúng không biến test/gate bắt buộc thành optional hoặc cho phép lỗi. Full mode
xóa `PYTEST_ADDOPTS`/`PYTEST_PLUGINS` kế thừa, tắt auto-load plugin bên thứ ba,
ghi đè `addopts` trong config và chọn rõ `pyproject.toml` đã kiểm tra. Hook
`conftest.py` của repository vẫn hoạt động. Discovery override có thể bỏ test
first-party thì bị từ chối. Runner từ chối coverage measurement/report override
có thể che thiếu coverage và dùng policy snapshot đã đóng băng cho cả hai lệnh.
Guarded coverage và repository validator yêu cầu section `.coveragerc` không có
prefix; mọi section `coverage:` bị từ chối vì Coverage.py ưu tiên dialect đó,
kể cả trong `.coveragerc`, làm policy hiệu lực khác policy đã xác minh. Source
và omission list dùng delimiter comma/LF; dạng comma/newline tương đương được
chấp nhận, nhưng Unicode-only line separator không thay thế được.

Fingerprint input được so sánh sau pytest và sau report ở measured và suite run.
Source, test hoặc metadata thay đổi làm kết quả mất hiệu lực. Fingerprint bind
cả identity directory được chấp nhận và byte file; thêm, xóa hoặc đổi tên
directory fixture rỗng cũng invalidate. Cả hai inventory được thu trong cùng
bounded traversal; loại trừ runtime cache ở root và mutation-cache fingerprint
không đổi. Đặt directory do test tạo trong temporary state được sở hữu, không
trong cây input repository. So sánh không phát hiện được edit rồi khôi phục giữa
hai lần đọc, vì vậy giữ input bất biến suốt lượt chạy. Nếu fail, sửa đúng nhóm
bằng focused check trước khi chạy full lại.

Mỗi runner child xóa `PYTHONINSPECT`, giữ nguyên environment parent. CLI từ chối
interpreter inspection đang hoạt động (`-i` hoặc environment) bằng status 2
trước khi chạy test/tạo runner output. Xóa biến sau startup không tắt REPL;
`SystemExit` có thể thành success khi gặp EOF. Admission này thoát process ngay,
kể cả khi diagnostics không flush được. Gọi `main()` trực tiếp vẫn trả status
cho caller; muốn debug tương tác thì chạy pytest ngoài guarded runner.

Guarded run cần completion receipt mới, có giới hạn trong temporary state thuộc
run, ngoài process status 0. Bootstrap gọi public pytest API, theo dõi collection
và từng test-finish event, giữ số node trùng. `--setup-only`/`--setup-plan` chỉ
kiểm tra fixture nên không chứng nhận run, kể cả khi có đủ finish event và
collection import cover source; measured-focused từ chối hai mode đó. Full/suite
xóa selection option kế thừa; focused không đo và `--setup-show` thông thường
vẫn dùng được, cũng như skip/xfail bình thường.

Failed report, internal error/interruption và session-finish status khác 0 làm
receipt vô hiệu. Wrapper quan sát event/status trước hook thông thường, kể cả
khi session-finish hoặc nested hook đổi status cuối về 0. Pytest còn có thể kết
luận `max_warnings` failure lúc terminal reporting sau status thành công; outer
wrapper đổi status về 0 cũng không được làm sạch receipt. Status khác 0 không
đổi được truyền tiếp, gồm warning-budget status 6. Không tự áp warning budget
nếu project không khai báo; warning trong mức và session hook thành công vẫn
hợp lệ.

Warning được đếm bằng public warning hook và so với `max_warnings` hiệu lực
(CLI ưu tiên config). Nhờ đó budget failure dựa trên warning event, không chỉ
status cuối có thể bị đổi hoặc thứ tự wrapper. Warning bị filter và warning
bắt bởi `pytest.warns()` không tính. Budget vẫn optional; giá trị hiệu lực sai
không thể chứng nhận thành công. Observer chỉ lưu count, không giữ warning
object/log không giới hạn. Exit 0 đột ngột, receipt thiếu hoặc session chưa hoàn
tất sẽ fail trước khi nhận runtime evidence hoặc báo coverage. Lỗi cleanup output
thuộc runner vẫn cần được xử lý trước khi truyền pytest status. Cơ chế phát hiện
execution thiếu, không sandbox test/plugin code đáng tin.

Measured run và suite mode xóa `PYTHONOPTIMIZE` trong mọi child để assertion
helper/production không bị interpreter tối ưu loại. Pytest rewrite assertion ở
test module, không phải mọi helper import. Environment parent và focused feedback
không đo lường giữ nguyên.

Measured và suite xóa mọi `COVERAGE_*` kế thừa trong child (force-config,
process-start, debug-output); environment caller không đổi. Measured check cần
cả hai source root khai báo là directory an toàn. Full report liệt kê rõ `.py`
và `.pyw` trong bounded inventory, gồm dotfile Coverage.py tự bỏ qua; focused
report liệt kê từng canonical source đã chọn để file undiscovered không biến
mất khỏi report. Không mode nào dùng data-only include filter làm denominator.

Report path rõ ràng dùng spelling trong inventory và native separator; lựa chọn
Windows không phân biệt hoa thường cùng bản sao được xác minh vẫn giữ đúng data
identity. Inventory identity nhập nhằng hoặc file ngoài inventory bị từ chối
trước khi coverage child chạy. Mọi source trong report scope phải compile như
Python trước khi child chạy, với giới hạn đọc từng file và tổng như inventory
budget. Không tạo bytecode, không thực thi source. Compile admission luôn dùng
semantics không tối ưu, bất kể `-O`, `-OO` hay `PYTHONOPTIMIZE` ở parent.
`dont_inherit` cô lập compiler/future flag nhưng không bỏ optimization; bỏ
assertion expression không được làm syntax sai trông như compile được.

Kiểm tra tôn trọng encoding declaration và từ chối syntax sai bất kể suffix hoa
hay thường, thay vì để Coverage.py âm thầm bỏ `.PY`/`.PYW` không parse được như
không phải Python. Full kiểm tra toàn inventory; measured-focused chỉ kiểm tra
canonical source đã chọn. Trước pytest, bootstrap measured child gọi public
Coverage `analysis2()` cho mọi report source. Compile được vẫn chưa đủ: Coverage
có thể parse khác source hợp lệ chứa form-feed rồi âm thầm bỏ parse error với
suffix `.PY`/`.PYW` viết hoa. Mọi analysis failure invalidate run, không thu hẹp
denominator. Dùng policy đóng băng, interpreter child không tối ưu và Coverage
instance riêng trong memory, chưa start, có data filename thuộc sở hữu. Không
harvest phép đo trước test, không phát `no-data-collected` quá sớm, không đổi
warning policy caller; không thực thi source và không đổi applicability của
suite/focused không đo.

Denominator gồm cả Python file chưa import trong namespace package. Runner và
repository validator từ chối `partial_also` cùng `[paths]` alias có thể che
branch thiếu hoặc gộp source identity khác nhau. Test path phải nằm trong
`tests/`, measured source trong hai script tree; absolute path nội bộ được
normalize, traversal và link boundary bị từ chối trước subprocess. Đây không
phải sandbox test, `conftest.py`, interpreter startup hoặc import Python tùy ý.

Tên file được chọn phải có path component portable: từ chối dấu hai chấm và
Windows alternate data stream vì byte stream không có trong inventory. Drive
prefix của absolute Windows path và dấu hai chấm trong pytest node ID riêng vẫn
hợp lệ. Inventory bind first-party root theo path semantics native, gồm spelling
case-insensitive trên Windows. Tên dependency/cache bị ignore trong các root đó
vẫn chứa input cần kiểm tra; mutation-cache snapshot selection mặc định không
đổi.

Trước coverage, mọi file hiện có trong ba copy omission đã chấp nhận phải khớp
byte canonical source trong `scripts/`. Canonical file thiếu, input không phải
file thường, path không an toàn, nội dung quá lớn hoặc byte khác đều fail.
Copy không có thì không có code để bỏ qua; isolated project một phần không cần
tạo mirror không dùng. Fingerprint vẫn bắt thay đổi về sau.

Guarded full/suite pytest từ chối redirect `log_file` và `cache_dir`. TOML
nesting-limit error thành admission error có kiểm soát trước test, kể cả metadata
không liên quan lồng sâu dưới trần config 64 KiB; không tăng parser limit và
metadata lồng bình thường vẫn được hỗ trợ. Repository validator báo cùng policy
error mà vẫn chạy các kiểm tra khác. Legacy mutation/pytest config consumer cũng
báo giới hạn numeric conversion native dưới dạng parse error, không tăng limit.
Coverage policy từ chối `debug_file` để config không chuyển report write sang
path ngoài. Các kiểm soát này không sandbox test code đáng tin hoặc pytest
focused/manual feedback.

Input inventory dùng chung nhận directory entry trước khi giữ và sắp xếp. Trần
10.000 entry tính cả thư mục, tên bị ignore và file thông thường; cây thư mục
rỗng không thể vượt qua trần này. Directory iterator được đóng khi quá giới
hạn hoặc đọc lỗi. Giới hạn byte từng file/tổng vẫn áp dụng cho byte thực đọc;
link và reparse-point boundary bị từ chối.

Verification inventory đi qua mọi thư mục trong `tests/` và cả hai first-party
script tree, kể cả `vendor`, `build`, `__pycache__`. Source và fixture ở đó
không biến mất theo rule ignore của mutation cache. Chỉ regular `.pyc`/`.pyo`
runtime bytecode bị bỏ qua trong các tree này; directory có suffix đó hoặc
fixture `.coverage.*` vẫn được inventory. Đặt output tạm ngoài cây input
first-party. Cache dependency/build ở root giữ nguyên exclusion; mutation-cache
snapshot selection không đổi. Bytecode cache là runtime state, không phải source
attestation; ranh giới trusted-interpreter/import vẫn áp dụng.

Guarded child run (`full`, `suite`, focused có `--source`) dùng prefix
`PYTHONPYCACHEPREFIX` duy nhất, thuộc run, thay cho bytecode cache repository
hoặc cache kế thừa. Pytest `--basetemp` và `cache_dir` cũng gắn vào thư mục con
riêng trong runtime root được sở hữu; chỉ xóa `PYTEST_DEBUG_TEMPROOT` khỏi child
environment. Repository/tox cache có sẵn và pytest temp session dùng chung
không được dùng hoặc dọn. Runtime root bị xóa khi thành công, lỗi hoặc
interruption nếu cleanup thành công. Cleanup không reset permission/attribute,
vì entry có thể cùng inode với file read-only không liên quan qua hardlink. Nếu
platform không xóa được, runner fail closed và nêu directory thuộc run còn lại
để kiểm tra; không clear read-only flag hay lờ cleanup failure.

Do cách ly cold cache, measured-focused không dùng lại selection `--lf` từ cache
cũ; dùng feedback không đo lường cho luồng đó. Isolation không sandbox write tùy
ý của test/plugin. Test ownership ghi canonical runtime path khi directory còn
tồn tại; alias system temp (như macOS `/var` và `/private/var`) không được gây
false failure. Alias test giữ temp alias và xác minh cleanup child được sở hữu.
Va chạm timestamp/size và unchecked-hash bytecode không thể thay code cũ cho
source hiện tại. Run và report dùng cùng prefix riêng, được dọn khi cleanup
thành công mà không clear cache có sẵn hoặc đổi environment caller. Cách này
tăng công việc cold-import ở lần xác minh cuối. Focused feedback không đo lường
giữ cache thường; cơ chế không chứng thực module parent đã load, custom importer
hoặc startup hook thực thi, và input vẫn phải bất biến trong suốt run.

First-party input phải là directory hoặc regular file. FIFO, socket, device và
entry không thường khác bị từ chối trước khi đọc hoặc chạy pytest/coverage;
âm thầm bỏ qua sẽ khiến inventory thiếu và có thể làm reader bị treo trên special
file. Test gồm POSIX FIFO native, đánh dấu không áp dụng trên Windows. Đây là
input admission, không phải process sandbox hoặc đảm bảo atomic trước race file
system.

Muốn chạy lại nhanh các lỗi đã ghi trong pytest local cache, dùng
`python -m pytest --lf --last-failed-no-failures none -q`. Cache rỗng sẽ không
chạy test nào nên không có bằng chứng xác minh. Cache ghi nhận selection, không
xác nhận code đã sửa đúng.

Runtime-only dùng `python scripts/run_tests.py suite`; thêm test path tường minh
cho subset đã khai báo như Linux mutation integration, hoặc dùng `--shard <id>`
cho Windows CI shard định nghĩa sẵn. Shard disjoint và hợp lại bằng toàn bộ
pytest file inventory. Suite mode cũng từ chối selection flag kế thừa và input
đã đổi. Runtime, coverage, canary và integration job của CI gọi các entrypoint
được bảo vệ này.

Chạy full-coverage gate cuối cùng và kiểm tra repository khác bằng:

```powershell
python scripts/run_tests.py full
python -m ruff format --check skills scripts tests
python -m ruff check skills scripts tests
python -m mypy --explicit-package-bases skills/repo-scaffold/scripts/check_community_health.py skills/repo-scaffold/scripts/audit_freshness.py skills/repo-scaffold/scripts/branch_protection_preflight.py skills/repo-scaffold/scripts/advanced_codeql_preflight.py skills/repo-scaffold/scripts/codeql_preflight.py skills/repo-scaffold/scripts/dependency_review_preflight.py skills/repo-scaffold/scripts/scorecard_preflight.py skills/repo-scaffold/scripts/ci_toolchain.py skills/repo-scaffold/scripts/pr_template_preflight.py skills/repo-scaffold/scripts/release_preflight.py skills/repo-scaffold/scripts/merge_settings_preflight.py skills/repo-scaffold/scripts/repository_settings_preflight.py skills/repo-scaffold/scripts/security_features_preflight.py skills/repo-scaffold/scripts/workflow_installation_preflight.py skills/repo-scaffold/scripts/sync_action_pins.py skills/repo-scaffold/scripts/validate_scaffold.py skills/repo-scaffold/scripts/markdown_body_preflight.py skills/repo-scaffold/scripts/render_pr_body_evidence.py scripts/audit_freshness.py scripts/audit_official_docs.py scripts/check_code_scanning_alerts.py scripts/coverage_policy.py scripts/merge_mutation_shards.py scripts/pr_template_preflight.py scripts/markdown_body_preflight.py scripts/prepare_mutation_cache.py scripts/python_support.py scripts/run_tests.py scripts/test_shards.py scripts/run_mutation_testing.py scripts/sync_action_pins.py scripts/sync_versioned_inputs.py scripts/update_pr_body.py scripts/render_pr_body_evidence.py scripts/validate_mutation_results.py scripts/validate_repository.py scripts/validate_workflows.py tests
python -m compileall -q skills/repo-scaffold/scripts scripts tests
python skills/repo-scaffold/scripts/ci_toolchain.py run-markdownlint
python scripts/validate_workflows.py
python scripts/validate_repository.py
```

Lệnh coverage thực thi branch-coverage floor 100% của repository trong
`.coveragerc`. Chạy boundary check bằng standard library thực tế của Python
min/latest đã khai báo. HTTP 308 redirect cho tài liệu được duyệt phải giữ GET/
HEAD kể cả khi redirect handler của runtime tối thiểu không hỗ trợ 308; vẫn giữ
giới hạn HTTPS/host, unsafe-method, body ownership và vòng lặp. Legacy body
publication báo cleanup failure mà không che write failure trước đó hoặc tuyên
bố rollback, và chỉ đăng ký cleanup sau khi allocation.

GitHub preflight client dùng chung nhận stdout/stderr theo byte budget trước khi
ghi temporary spool. Giới hạn mỗi stream và tổng inspection được áp dụng ngay
trong lúc capture, không đợi CLI exit. Cần EOF trên cả hai pipe và CLI hoàn tất
trước khi decode; timeout, output vượt giới hạn hoặc spool write dở là lỗi,
không phải evidence một phần. Windows native dùng pipe readiness thay vì
`select` chỉ hỗ trợ socket; POSIX dùng pipe descriptor được select. Những check
này không chứng nhận allocation bên trong SDK, dừng process con, origin của HTTP
redirect hoặc request count thực tế. Giữ các yêu cầu audit độc lập đó.

Metrics preflight có `gh` phân biệt client attempt với wire request.
`github_client_requests` là counter bị tính ở client admission;
`github_api_requests` vẫn là alias tương thích, có nhãn
`github_api_requests_unit: client-request-attempts`. Số transport là
`github_http_requests: null`, state `not-measured`, tuyệt đối không phải 0 hoặc
bằng chứng đã đạt. Không thể đếm redirect hay request nội bộ SDK từ body CLI trả
về. Metrics chẩn đoán không cấp quyền ghi hoặc chứng minh quota HTTP thực tế,
cũng không chứng minh nguồn gốc response ban đầu.

Mutation testing chạy hằng ngày và dispatch thủ công vì lượt đầy đủ tốn kém hơn
PR check bắt buộc nhiều. Workflow lập kế hoạch mọi mutant, chạy 128 Linux shard
chính xác và chỉ merge assignment đầy đủ, không chồng lấn trước khi áp gate.
mutmut cần hệ điều hành hỗ trợ `fork`, vì vậy chạy trên Linux/macOS hoặc WSL trên
Windows.

Hosted shard dùng read-only diagnostic sampler quanh engine command không đổi.
Sampler ghi counter memory system/cgroup và engine/process do nó sở hữu trong
giới hạn, cùng chính xác mutant key được shard phân công; không ghi environment
value hay command line tùy ý. Danh sách worker trống nghĩa là identity không khả
dụng, không chứng minh không có mutant nào chạy. Memory tiến trình con giới hạn
16 process và vẫn quan sát được khi title không bind mutant key. Sampling kiểm
tra process start time, giới hạn process scan, ghi tối đa 360 mẫu cách nhau 30
giây và tổng output tối đa 2 MiB. Counter thiếu và scan một phần vẫn được nêu
rõ; riêng cgroup OOM counter không xác định victim hoặc lý do runner dừng.
Sampler không thể cấp verdict: wrapper giữ engine exit status và dừng observer
khi thoát. Vẫn cần CI/workflow validation và aggregate mutation đầy đủ.

Runner thu gom cycle sau pytest không còn được tham chiếu ngay trước khi startup
upstream đóng băng GC để fork. Trên Linux nếu allocator process có
`malloc_trim`, nó trả heap page đã free về OS; allocator khác bỏ qua bước trim
tùy chọn. Module, association, verdict, trạng thái GC bật/tắt, worker count và
startup upstream còn sống giữ nguyên. Việc này giảm allocation baseline còn
giữ, không giới hạn memory worker đang sống hoặc chứng minh vì sao hosted runner
nhận tín hiệu shutdown. Linux integration test native xác minh reclaim heap và
giữ live state; chỉ run đúng commit đã hoàn tất mới chứng minh shard thành công.

Nếu lock resolve LibCST thành source distribution do không có wheel tương thích,
hãy chuẩn bị Rust toolchain tương thích và native build tool của platform trong
môi trường build cô lập. Xác định compiler requirement từ source đã pin và xác
minh hash, gồm Cargo manifest; đừng suy ra wheel Linux đồng nghĩa hỗ trợ macOS
Intel. Pip hash lock không chứng minh source build thành công, cũng không cài
Rust hoặc system linker/SDK. Ghi toolchain và build/runtime result thực tế trên
platform đích hoặc báo check deferred. Không hạ score gate, đổi pin hoặc gắn
nhãn pass runtime cho kiểm tra chỉ đọc metadata để né build không khả dụng.

```bash
python -m pip install --require-hashes --requirement requirements-mutation.txt
python scripts/run_mutation_testing.py --max-children 4
mutmut export-cicd-stats
mutmut results --all true > mutants/mutation-results.txt
python scripts/validate_mutation_results.py
```

Repository đã thực thi branch coverage 100%. Vì vậy mutmut dùng call-based
selection mặc định thay vì Coverage.py line prepass tùy chọn; cách này giữ
association của in-process trampoline đáng tin cậy mà không thu hẹp mutation
scope. Result validator fail với mutant bị skip, chưa test, đáng ngờ, gián đoạn
hoặc crash và yêu cầu mutation-score floor 100.00% trên mutant killed, timeout
và survivor. Timeout được tính là detected vì mutant khiến test process có giới
hạn không kết thúc; nó vẫn hiện trong summary. Giữ generated mutant source,
metadata từng file, test-association data và result summary để chẩn đoán. Không
được bỏ mutant hợp lệ chỉ để score đạt; phân loại mutant tương đương trong lúc
review và chỉ nâng floor dựa trên run đầy đủ, có thể lặp lại.

`validate_workflows.py` chạy actionlint rồi chạy riêng ShellCheck đã review trên
các Bash block trích xuất. Markdown check bao phủ mọi `.md` thuộc dự án, layout
README, scaffold marker chưa xử lý, relative link và Markdown issue/PR template.
Nếu thêm PowerShell block, chạy thêm PSScriptAnalyzer trên từng block.

## Mở pull request

Trước khi tạo hoặc sửa pull request, chạy
`python scripts/pr_template_preflight.py --title "<title>"`. Với review tập
trung cho security, deployment hoặc dependency update mà title không có mapping
bắt buộc, thêm `--template security`, `--template deployment` hoặc
`--template dependency-update`.

Sau khi tạo body file UTF-8, chạy lại preflight với `--body-file <path>`; thêm
`--require-structure` để xác minh heading và checklist bắt buộc của template đã
chọn trước khi sửa GitHub. Draft item có thể chưa tick; readiness được template
gate kiểm tra riêng. Nếu PR cần đóng issue sau khi merge, thêm dòng riêng
`Fixes #123`, `Closes #123` hoặc `Resolves #123`. Khi có dòng đó preflight xác
minh cú pháp, nhưng không bắt mọi PR phải tham chiếu issue.

Khi commit thay đổi hành vi hoặc policy, đưa field summary có cấu trúc vào
commit body để PR được tạo giữ lại ngữ cảnh review:

```text
Why: the user or maintainer outcome
Root cause: the confirmed technical cause
Changes: the main implementation or policy changes
Verification: the focused tests or checks run
```

Field phải có dấu hai chấm và có thể tiếp tục bằng paragraph hoặc bullet ở các
dòng sau. Ghi mọi thay đổi hành vi quan trọng, kể cả fix và revert về sau. Mỗi
paragraph prose và bullet trong commit body phải nằm trên một dòng vật lý; không
hard-wrap ở 72 hoặc 80 ký tự. Ngăn cách paragraph và field bằng dòng trống. Giữ
nguyên newline có chủ ý trong code block. Quy ước repository này thay cho lời
khuyên wrap commit body chung. Renderer vẫn chấp nhận message cũ đã wrap mà
không sửa commit. Commit subject không chứng minh root cause; validation ghi
trong commit chỉ là evidence cho revision đó, không đảm bảo các commit sau.

Với PR dài, một commit có thể cung cấp summary đã review của toàn diff bằng cách
thêm `PR-summary-base: <full-current-base-SHA>` trước cả bốn field. Summary bao
phủ PR đến commit chứa nó. Renderer đưa mọi thay đổi về sau vào, từ chối khi base
đổi và fail nếu phải bỏ chủ đề để vừa giới hạn paragraph hoặc năm bullet. Khi
đó, review toàn bộ diff và cập nhật cumulative summary. Summary phải bao quát
hành vi chính và thay đổi ranh giới an toàn của PR, không chỉ công cụ PR-body
hoặc tài liệu giúp việc review dễ hơn.

Workflow `pr-body-sync` của repository render toàn bộ pull-request body từ bằng
chứng PR API chính xác khi PR được mở, mở lại hoặc có commit mới. Workflow rút
commit subject, field có cấu trúc và inventory file thay đổi đầy đủ, gắn với base
và head bất biến. Purpose, cause, key changes và verification từ commit do tác
giả báo cáo, gắn với source SHA đầy đủ; nhắc nhở tab Checks hiện tại được tạo
riêng. File inventory xác minh pagination và số file trong PR; nó không chứng
minh chủ đề thay đổi hoặc đối chiếu claim tự sự với patch. Trạng thái check trực
tiếp vẫn nằm ở tab Checks của GitHub. Workflow không đọc PR-body narrative
hardcode. PR template đáng tin đã chọn cung cấp heading và checklist contract;
các phần được tạo sẽ thay từ evidence hiện tại. Renderer khớp alias heading
chính xác ngoài code, comment và raw HTML, đồng thời giữ section riêng cho
reviewer và kế hoạch verification/monitoring kết hợp.

CLI đối chiếu cấu trúc đã render với template đáng tin trước khi thay output;
body một phần bị từ chối trước khi workflow sửa GitHub. Khi gọi ngoài
repository mục tiêu, dùng `--repository-root`. Workflow giữ nguyên body
Dependabot gốc và Release Please cùng repository theo rule author đã xác thực,
sau khi xác minh đầy đủ PR evidence đã bind; tên bot hoặc branch giống release
không đủ để miễn kiểm tra. Workflow giới hạn API response và evidence tổng hợp,
chạy Markdown/PR-template preflight trước khi sửa, đọc lại head, base, title,
repository và body hiện tại ngay sát mutation rồi xác minh toàn body sau khi
GitHub chấp nhận. Evidence thiếu, sai định dạng, cũ, chưa đủ hoặc vượt giới hạn
đều fail closed.

GitHub PR commit endpoint hỗ trợ tối đa 250 commit, file endpoint tối đa 3.000
file. Template preflight xác minh cấu trúc và bố cục prose; cần đối chiếu
description cuối với toàn bộ diff để xác nhận đủ nội dung. Tiêu chí nghiệm thu,
ghi chú rủi ro và kế hoạch rollout/rollback do con người viết vẫn phải review.
GitHub pull-request update endpoint không có tham số conditional body-write.
Nếu con người sửa đồng thời sau lần đọc cuối của workflow nhưng trước lần update,
nội dung đó có thể bị ghi đè; workflow rút ngắn khoảng này và xác minh body sau
đó.

Dùng PR template mặc định cho thay đổi thông thường. Chỉ chọn template chuyên
biệt khi đúng quy trình review:

- `feature.md` cho năng lực mới hoặc mở rộng đáng kể;
- `bugfix.md` cho lỗi đã xác nhận và regression coverage;
- `documentation.md` cho thay đổi chỉ về tài liệu hoặc do tài liệu dẫn dắt;
- `security.md` cho thay đổi bảo mật có thể công bố an toàn. Với lỗ hổng chưa
  công bố, làm theo `SECURITY.md` thay vì mở PR công khai;
- `deployment.md` cho kế hoạch triển khai, rollout hoặc rollback;
- `dependency-update.md` cho dependency trực tiếp hoặc bắc cầu; hoặc
- template mặc định cho thay đổi thông thường.

Với PR title Conventional Commit bắt đầu bằng `feat`, `fix` hoặc `docs`, lần
lượt dùng `feature.md`, `bugfix.md` hoặc `documentation.md`. Gate `pr-template`
thực thi mapping đó. Loại title được hỗ trợ khác dùng template mặc định, trừ
khi thay đổi cần review riêng về security, deployment hoặc dependency update.

Giữ nguyên marker `repo-scaffold:pr-template` đã chọn, mọi heading bắt buộc và
required checklist. Chỉ thêm item áp dụng trong `If applicable`, hoặc bỏ toàn
bộ section đó. Gate bắt buộc `pr-template` từ chối body không có đúng một marker
đáng tin hoặc checklist bắt buộc chưa đầy đủ. Draft PR được để item bắt buộc
chưa tick; trước khi chuyển sang ready for review, chỉ tick sau khi hoàn tất.

Trong PR, nêu:

- vấn đề và lý do cần thay đổi;
- file cùng hành vi đã sửa;
- nguồn chính thức cho hành vi được định nghĩa bên ngoài;
- lệnh xác minh chính xác và kết quả;
- giới hạn còn lại hoặc check chưa chạy được.

Để thay đổi unstaged và uncommitted trừ khi maintainer repository yêu cầu rõ
thao tác Git.

## Phát hành

Release được tự động từ `main` qua Release Please.

1. Dùng Conventional Commit title cho thay đổi merge vào `main` và đợi mọi
   required check.
2. Review Release Please pull request. Nó cập nhật `CHANGELOG.md`, `version.txt`,
   `.release-please-manifest.json`, `.codex-plugin/plugin.json` và
   `.claude-plugin/plugin.json` theo SemVer đề xuất.
3. Sau khi check đạt, merge pull request release. Release Please tạo tag và
   GitHub Release dạng draft, rồi gọi release engine dùng chung.
4. Xác minh asset release đã đính kèm, provenance attestation đạt và release đã
   được publish:

   ```bash
   gh attestation verify repo-scaffold-plugin-vX.Y.Z.zip \
     --repo MinhThang1009/repo-scaffold-plugin \
     --signer-workflow MinhThang1009/repo-scaffold-plugin/.github/workflows/release.yml
   ```

Workflow cần fine-grained PAT lưu trong `RELEASE_PLEASE_TOKEN`, chỉ giới hạn
trong repository này với **Contents: Read and write** và **Pull requests: Read
and write**. Cấp thêm **Issues: Read and write** vì Release Please quản lý label
của release PR. Không đưa token vào file, commit, command output, issue hoặc tin
nhắn.

Release engine dùng quyền contents read-only để build plugin archive, chuyển nó
sang attestation job riêng (chỉ job này nhận OIDC và attestation permission),
sau đó cho contents-write publish job publish đúng draft Release tương ứng.
Không privileged job nào checkout hoặc chạy project code. Engine từ chối thay
asset của Release đã publish; hãy phát hành version mới.
