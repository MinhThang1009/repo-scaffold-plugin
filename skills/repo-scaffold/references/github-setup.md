# GitHub Configuration Reference

Exact `gh` commands for step 5 (configure GitHub). Confirm each outward-facing action before running it.

This reference supports GitHub.com only. Before using any command, verify the remote host is exactly `github.com`, record its canonical `OWNER/REPO`, and substitute `github.com/OWNER/REPO` for repository CLI commands plus `--hostname github.com` for API commands. Stop remote configuration for GitHub Enterprise Server or GHE.com; the bundled workflows and commands are not a portable enterprise-host setup. Do not rely on `GH_HOST`, the current directory, or gh's default repository.

NOTE (Windows/Git-Bash): `gh api` paths must NOT start with a leading slash, or the shell rewrites them to a filesystem path (`C:/Program Files/Git/...`). Use `gh api --hostname github.com repos/OWNER/REPO/...`, never `gh api --hostname github.com /repos/...`.

Preflight `inspection_complete` must be a JSON Boolean. Reject strings, numbers,
arrays, missing values, and other ambiguous types before using the decision to
authorize a write or asset copy; PowerShell truthiness is not protocol validation.
Each verdict must be one JSON object with scalar text identity and decision
fields. Reject array roots before parsing so host-specific enumeration cannot
turn a one-element array into a valid-looking object. Validate mutation-plan
collections and input types before casts, comparisons, or normalization.

## Contents

- [Repository identity preflight](#repository-identity-preflight)
- [Description and topics](#description-and-topics)
- [Repository communication features](#repository-communication-features)
- [Workflow installation preflight](#workflow-installation-preflight)
- [Inherited community-health policy](#inherited-community-health-policy)
- [Branch protection (classic)](#branch-protection-classic)
- [Ruleset compatibility (inspect only)](#ruleset-compatibility-inspect-only)
- [Labels](#labels)
- [Dependabot](#dependabot)
- [Security features](#security-features)
- [Merge settings](#merge-settings)
- [release-please token](#release-please-token-release_please_token)
- [Verify](#verify)

## Repository identity preflight

Caller controls are genuine Booleans, not strings, numbers, collections or
null values interpreted by truthiness. Release attestation/plan/secret checks,
merge-method removal/auto-merge requests and CodeQL absence/admin requirements
reject malformed library inputs before inspection. CLI `store_true` defaults
remain unchanged; missing optional CodeQL administration requirements retain
their documented false default. A typed confirmation is not evidence of its
user origin, target-bound approval or effective platform entitlement.

Resolve the target before the first GitHub query. An argument-free `gh repo view` can follow gh's configured default repository; for a fork cloned with [`gh repo clone`](https://cli.github.com/manual/gh_repo_clone), GitHub CLI sets the parent repository as that default unless `--no-upstream` is used. Treat `gh repo set-default --view` as diagnostic output only.

```powershell
function ConvertTo-GitHubRepository {
  param([Parameter(Mandatory)][string]$RemoteUrl)

  $owner = $null
  $repo = $null
  $uri = $null
  $isAbsoluteUri = [Uri]::TryCreate($RemoteUrl, [UriKind]::Absolute, [ref]$uri)
  if ($isAbsoluteUri -and
      [System.StringComparer]::OrdinalIgnoreCase.Equals($uri.Host, "github.com")) {
    if ($uri.Scheme -notin @("https", "ssh")) {
      throw "A GitHub.com remote uses an unsupported URL scheme."
    }
    if (($uri.Scheme -eq "https" -and -not [string]::IsNullOrEmpty($uri.UserInfo)) -or
        ($uri.Scheme -eq "ssh" -and $uri.UserInfo -ne "git")) {
      throw "A GitHub.com remote contains unsupported user information; do not print the URL because it may contain credentials."
    }
    $segments = @($uri.AbsolutePath.Trim('/') -split '/')
    if ($segments.Count -ne 2) { throw "A GitHub.com remote does not identify one OWNER/REPO pair." }
    $owner, $repo = $segments
  } elseif ($isAbsoluteUri) {
    return $null
  } else {
    $scpMatch = [regex]::Match(
      $RemoteUrl,
      '^(?i:git@github\.com):([^/]+)/([^/]+)$'
    )
    if ($scpMatch.Success) {
      $owner = $scpMatch.Groups[1].Value
      $repo = $scpMatch.Groups[2].Value
    } elseif ($RemoteUrl -match '(?i)(?:^|@)github\.com[:/]') {
      throw "A GitHub.com remote URL is malformed or unsupported; do not guess its repository."
    } else {
      return $null
    }
  }

  if ($repo.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
    $repo = $repo.Substring(0, $repo.Length - 4)
  }
  if ([string]::IsNullOrWhiteSpace($owner) -or [string]::IsNullOrWhiteSpace($repo) -or
      $owner -in @(".", "..") -or $repo -in @(".", "..") -or
      $owner -notmatch '^[A-Za-z0-9_.-]+$' -or
      $repo -notmatch '^[A-Za-z0-9_.-]+$') {
    throw "A GitHub.com remote has an invalid OWNER/REPO value."
  }
  return "$owner/$repo"
}

$remoteNames = @(git -C $REPO_ROOT remote)
if ($LASTEXITCODE -ne 0) { throw "Failed to list git remotes." }

$remoteRepositoryMappings = @()
foreach ($remoteName in $remoteNames) {
  $remoteUrlOutput = git -C $REPO_ROOT remote get-url --all -- $remoteName 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to read the fetch URLs for remote '$remoteName'."
  }
  foreach ($remoteUrl in @($remoteUrlOutput)) {
    $repository = ConvertTo-GitHubRepository ([string]$remoteUrl)
    if ($null -ne $repository) {
      $remoteRepositoryMappings += [pscustomobject]@{
        RemoteName = $remoteName
        Repository = $repository
      }
    }
  }
}

$repositoryCandidates = @(
  $remoteRepositoryMappings.Repository | Sort-Object -Unique
)
# Display this canonical mapping, never the raw URLs. A raw HTTPS remote can contain credentials.
$remoteRepositoryMappings
$configuredGhDefault = gh repo set-default --view 2>&1
# Diagnostic only; never add $configuredGhDefault to $repositoryCandidates or use it to select one.
```

When no GitHub.com candidate exists, stop remote discovery and continue as local-only. When one exists, use it. When several exist, display the remote mapping and ask the user to choose; never prefer `origin`, `upstream`, or gh's default. After selection, query only the explicit repository and cross-check the response:

```powershell
$selectedRepository = "OWNER/REPO" # selected from the verified remote candidates
$repoViewOutput = gh repo view "github.com/$selectedRepository" `
  --json nameWithOwner,url,owner,defaultBranchRef,visibility,isFork,isArchived 2>&1
if ($LASTEXITCODE -ne 0) { throw "Failed to read the selected GitHub repository." }
$repoViewText = $repoViewOutput | Out-String
if (-not $repoViewText.TrimStart().StartsWith("{")) {
  throw "Selected repository view must be one JSON object; do not bind the target."
}
$repoView = $repoViewText | ConvertFrom-Json -ErrorAction Stop
if ($repoView -isnot [System.Management.Automation.PSCustomObject] -or
    $repoView.nameWithOwner -isnot [string] -or $repoView.url -isnot [string]) {
  throw "Selected repository view has an invalid identity schema; do not bind the target."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  $repoView.nameWithOwner,
  $selectedRepository
)) {
  throw "GitHub returned a different repository than the selected remote candidate."
}
$repoHost = ([Uri]$repoView.url).Host
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($repoHost, "github.com")) {
  throw "The selected repository is not hosted on GitHub.com."
}

$selectedIdentityOutput = gh api --hostname github.com "repos/$selectedRepository" 2>&1
if ($LASTEXITCODE -ne 0) { throw "Could not bind the selected repository identity; do not mutate." }
$selectedIdentityText = $selectedIdentityOutput | Out-String
if (-not $selectedIdentityText.TrimStart().StartsWith("{")) {
  throw "Selected repository identity must be one JSON object; do not bind the target."
}
$selectedIdentity = $selectedIdentityText | ConvertFrom-Json -ErrorAction Stop
if ($selectedIdentity -isnot [System.Management.Automation.PSCustomObject] -or
    $selectedIdentity.full_name -isnot [string] -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals($selectedIdentity.full_name, $selectedRepository) -or
    ($selectedIdentity.id -isnot [int] -and $selectedIdentity.id -isnot [long]) -or
    $selectedIdentity.id -le 0) {
  throw "Selected repository identity is missing, malformed, or mismatched; do not mutate."
}
$SELECTED_REPOSITORY_ID = $selectedIdentity.id

function Assert-SelectedRepositoryId {
  param([Parameter(Mandatory)][AllowNull()][object]$RepositoryId)
  if (($SELECTED_REPOSITORY_ID -isnot [int] -and $SELECTED_REPOSITORY_ID -isnot [long]) -or
      $SELECTED_REPOSITORY_ID -le 0 -or
      ($RepositoryId -isnot [int] -and $RepositoryId -isnot [long]) -or
      $RepositoryId -le 0 -or $RepositoryId -ne $SELECTED_REPOSITORY_ID) {
    throw "Numeric repository identity is unverified or changed since discovery; stop remote mutation and review the target."
  }
}
```

Preserve `SELECTED_REPOSITORY_ID` for this approved target. Do not reseed it from
an unexpected later response. Repository-settings, security-feature and
merge-settings, dependency-review, Scorecard, release, workflow-installation
and CodeQL default/advanced
preflights accept
`--expected-repository-id` and return a typed
`repository_id`; pass the discovery value on every repeated inspection and
check the returned value before trusting a mutation verdict. Their optional
unbound inspection mode returns `bind-repository-identity-before-mutation`;
it discovers the current ID only and must not authorize a mutation or replace
the ID captured during approved discovery. Other feature preflights must still
be reviewed for equivalent identity binding before considering the lifecycle
complete. Named REST calls have no assumed atomic numeric-ID precondition;
this check does not claim to eliminate all check-to-write races.

Pass `github.com/OWNER/REPO` to every later repository-scoped `gh` command. Never fall back to an argument-free query after this preflight.

## Description and topics

Run the bundled `scripts/repository_settings_preflight.py` immediately before
changing basic repository metadata or communication settings. It is read-only
and fail-closed: it binds the GitHub response to the explicit `OWNER/REPO`,
rejects archived or disabled repositories, requires current administration permission, and
binds the approved description, topic, Issues, and Discussions requests to the
final mutation plan. Do not run `gh repo edit` when this preflight is absent,
inconclusive, or approves a different request.
The preflight enforces GitHub's 50-character topic names and 20-topic limit,
including requested additions combined with the preserved current set. It does
not authorize removing an existing topic to make room. GitHub CLI performs
repository-field and topic updates as separate concurrent API requests; a
combined edit can therefore be partially applied. Read back state even when the
edit exits unsuccessfully, and report partial or unverified outcomes.

```powershell
# Keep user/repository text as data. Do not generate a command string and invoke it.
$description = Read-Host "One-line repository description"
if ([string]::IsNullOrWhiteSpace($description)) {
  throw "Repository description must be non-empty."
}
$topics = @("topic1", "topic2") # confirmed GitHub topic slugs
$repositorySettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/repository_settings_preflight.py"
if (-not (Test-Path -LiteralPath $repositorySettingsPreflight -PathType Leaf)) {
  throw "The bundled repository-settings preflight is missing; do not edit repository metadata."
}
$metadataPreflightArguments = @(
  "--repository", "OWNER/REPO", "--hostname", "github.com",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--set-description", "--description", $description
)
if ($topics.Count -gt 0) {
  $metadataPreflightArguments += "--set-topics"
  foreach ($topic in $topics) { $metadataPreflightArguments += @("--topic", $topic) }
}
$metadataPreflightOutput = python $repositorySettingsPreflight @metadataPreflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Repository metadata inspection is inconclusive; do not edit metadata. $($metadataPreflightOutput | Out-String)"
}
try {
  $metadataPreflightText = $metadataPreflightOutput | Out-String
  if (-not $metadataPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
  $metadataPreflight = $metadataPreflightText | ConvertFrom-Json -ErrorAction Stop
} catch {
  throw "Repository-settings preflight returned invalid JSON; do not edit metadata."
}
Assert-SelectedRepositoryId -RepositoryId $metadataPreflight.repository_id
if ($metadataPreflight -isnot [pscustomobject] -or
    $metadataPreflight.repository -isnot [string] -or
    $metadataPreflight.decision -isnot [string] -or
    $metadataPreflight.inspection_complete -isnot [bool] -or
    -not $metadataPreflight.inspection_complete -or
    $metadataPreflight.decision -ne "may-configure-repository-settings" -or
    @($metadataPreflight.requested_mutations) -notcontains "description") {
  throw "Repository-settings preflight did not approve the requested metadata mutations."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$metadataPreflight.repository, "OWNER/REPO"
)) {
  throw "Repository-settings preflight returned a different repository; do not mutate."
}
$approvedMetadata = $metadataPreflight.requested_settings
if ($approvedMetadata -isnot [pscustomobject] -or
    $approvedMetadata.description -isnot [string] -or
    $approvedMetadata.topics -isnot [Array] -or
    @($approvedMetadata.topics | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
    $metadataPreflight.requested_mutations -isnot [Array] -or
    @($metadataPreflight.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0) {
  throw "Repository-settings preflight did not return the approved metadata input."
}
$expectedMetadataMutations = @("description")
if ($topics.Count -gt 0) { $expectedMetadataMutations += "topics" }
if ($metadataPreflight.requested_mutations.Count -ne $expectedMetadataMutations.Count -or
    $null -ne (Compare-Object -ReferenceObject $expectedMetadataMutations -DifferenceObject $metadataPreflight.requested_mutations -CaseSensitive)) {
  throw "Metadata preflight does not approve exactly the requested mutations; do not edit metadata."
}
$approvedTopics = @($approvedMetadata.topics)
if ($topics.Count -gt 0 -and
    ($metadataPreflight.current_topics -isnot [Array] -or
     @($metadataPreflight.current_topics | Where-Object { $_ -isnot [string] }).Count -gt 0)) {
  throw "Preflight did not provide the preserved topic set; do not edit metadata."
}
$previousTopics = @()
if ($topics.Count -gt 0) { $previousTopics = @($metadataPreflight.current_topics) }
if ($approvedMetadata.description -cne $description -or
    $approvedTopics.Count -ne $topics.Count -or
    $null -ne (Compare-Object -ReferenceObject @($topics) -DifferenceObject $approvedTopics -CaseSensitive)) {
  throw "Repository-settings preflight input does not match the metadata mutation."
}
$topicArgs = @()
foreach ($topic in $topics) { $topicArgs += @('--add-topic', $topic.ToLowerInvariant()) }
$editOutput = & gh repo edit github.com/OWNER/REPO --description $description @topicArgs 2>&1
$editExitCode = $LASTEXITCODE

$metadataOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Metadata change may be partially applied and could not be verified. Edit exit: $editExitCode. $($metadataOutput | Out-String)"
}
$metadata = ($metadataOutput | Out-String) | ConvertFrom-Json
Assert-SelectedRepositoryId -RepositoryId $metadata.id
if ($metadata.full_name -isnot [string] -or
    -not [StringComparer]::OrdinalIgnoreCase.Equals($metadata.full_name, "OWNER/REPO") -or
    $metadata.description -isnot [string] -or $metadata.topics -isnot [Array] -or
    @($metadata.topics | Where-Object { $_ -isnot [string] }).Count -gt 0) {
  throw "Metadata change may be partially applied; readback schema or repository binding is invalid."
}
$actualTopics = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($topic in @($metadata.topics)) { [void]$actualTopics.Add([string]$topic) }
$missingTopics = @($topics | Where-Object { -not $actualTopics.Contains($_) })
$missingPreviousTopics = @($previousTopics | Where-Object { -not $actualTopics.Contains($_) })
if ($metadata.description -cne $description -or $missingTopics.Count -gt 0 -or
    $missingPreviousTopics.Count -gt 0) {
  throw "Metadata change may be partially applied or concurrently changed. Description matches: $($metadata.description -ceq $description). Missing requested topics: $($missingTopics -join ', '). Missing preserved topics: $($missingPreviousTopics -join ', ')."
}
if ($editExitCode -ne 0) {
  Write-Warning "The edit failed, but readback verified the requested and preserved metadata state. $($editOutput | Out-String)"
}
```

A non-empty description is required for a 100% community profile. Topics aid discovery.

## Repository communication features

Inspect these settings before generating issue templates or communication links:

```bash
gh repo view github.com/OWNER/REPO --json isArchived,hasIssuesEnabled,hasDiscussionsEnabled
```

Archived repositories are read-only; do not attempt remote configuration for them. When an active repository has Issues or Discussions disabled, ask before enabling either feature. Run approved mutations separately, then re-query the state before rendering dependent files:

```powershell
# Set these only from explicit user confirmation.
$enableIssuesRequested = $false
$enableDiscussionsRequested = $false
if ($enableIssuesRequested -isnot [bool] -or $enableDiscussionsRequested -isnot [bool]) {
  throw "Communication choices must be explicit Boolean values; do not infer approval."
}

if ($enableIssuesRequested -or $enableDiscussionsRequested) {
  $repositorySettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/repository_settings_preflight.py"
  if (-not (Test-Path -LiteralPath $repositorySettingsPreflight -PathType Leaf)) {
    throw "The bundled repository-settings preflight is missing; do not enable communication features."
  }
  $communicationPreflightArguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
  if ($enableIssuesRequested) { $communicationPreflightArguments += "--enable-issues" }
  if ($enableDiscussionsRequested) { $communicationPreflightArguments += "--enable-discussions" }
  $communicationPreflightOutput = python $repositorySettingsPreflight @communicationPreflightArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Communication-feature inspection is inconclusive; do not mutate. $($communicationPreflightOutput | Out-String)"
  }
  try {
    $communicationPreflightText = $communicationPreflightOutput | Out-String
    if (-not $communicationPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $communicationPreflight = $communicationPreflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Repository-settings preflight returned invalid JSON; do not mutate."
  }
  Assert-SelectedRepositoryId -RepositoryId $communicationPreflight.repository_id
  if ($communicationPreflight -isnot [pscustomobject] -or
      $communicationPreflight.repository -isnot [string] -or
      $communicationPreflight.decision -isnot [string] -or
      $communicationPreflight.inspection_complete -isnot [bool] -or
      -not $communicationPreflight.inspection_complete -or
      $communicationPreflight.decision -ne "may-configure-repository-settings") {
    throw "Repository-settings preflight did not approve the requested communication mutations."
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$communicationPreflight.repository, "OWNER/REPO"
  )) {
    throw "Repository-settings preflight returned a different repository; do not mutate."
  }
  $approvedCommunication = $communicationPreflight.requested_settings
  if ($approvedCommunication -isnot [pscustomobject] -or
      $approvedCommunication.issues -isnot [bool] -or
      $approvedCommunication.discussions -isnot [bool] -or
      $approvedCommunication.issues -ne $enableIssuesRequested -or
      $approvedCommunication.discussions -ne $enableDiscussionsRequested) {
    throw "Repository-settings preflight input does not match the communication mutation."
  }
  $expectedCommunicationMutations = @()
  if ($enableIssuesRequested) { $expectedCommunicationMutations += "issues" }
  if ($enableDiscussionsRequested) { $expectedCommunicationMutations += "discussions" }
  if ($communicationPreflight.requested_mutations -isnot [Array] -or
      @($communicationPreflight.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $communicationPreflight.requested_mutations.Count -ne $expectedCommunicationMutations.Count -or
      $null -ne (Compare-Object -ReferenceObject $expectedCommunicationMutations -DifferenceObject $communicationPreflight.requested_mutations -CaseSensitive)) {
    throw "Communication preflight does not approve exactly the requested mutations."
  }
}

function Test-FreshCommunicationPreflight {
  param([Parameter(Mandatory)][ValidateSet("issues", "discussions")][string]$Feature)

  $arguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
  if ($Feature -eq "issues") { $arguments += "--enable-issues" }
  if ($Feature -eq "discussions") { $arguments += "--enable-discussions" }
  $output = python $repositorySettingsPreflight @arguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Write-Warning "Fresh $Feature preflight is inconclusive; skip this mutation. $($output | Out-String)"
    return $false
  }
  try {
    $preflightText = $output | Out-String
    if (-not $preflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $result = $preflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    Write-Warning "Fresh $Feature preflight returned invalid JSON; skip this mutation."
    return $false
  }
  if ($result -isnot [pscustomobject] -or
      $result.repository -isnot [string] -or $result.decision -isnot [string] -or
      $result.requested_mutations -isnot [Array] -or
      @($result.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $result.requested_settings -isnot [pscustomobject] -or
      $result.requested_settings.issues -isnot [bool] -or
      $result.requested_settings.discussions -isnot [bool]) {
    Write-Warning "Fresh communication preflight has an invalid schema; skip this mutation."
    return $false
  }
  $mutations = @($result.requested_mutations)
  $expectedIssues = $Feature -eq "issues"
  $expectedDiscussions = $Feature -eq "discussions"
  Assert-SelectedRepositoryId -RepositoryId $result.repository_id
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      $result.decision -ne "may-configure-repository-settings" -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      ) -or $mutations.Count -ne 1 -or $mutations[0] -cne $Feature -or
      $null -eq $result.requested_settings -or
      $result.requested_settings.issues -ne $expectedIssues -or
      $result.requested_settings.discussions -ne $expectedDiscussions) {
    Write-Warning "Fresh $Feature preflight did not approve exactly that setting for OWNER/REPO; skip this mutation."
    return $false
  }
  return $true
}

if ($enableIssuesRequested) {
  if (Test-FreshCommunicationPreflight -Feature "issues") {
    $issuesOutput = & gh repo edit github.com/OWNER/REPO --enable-issues 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Issues could not be enabled; issue-dependent output will be omitted. $($issuesOutput | Out-String)"
    }
  }
}
if ($enableDiscussionsRequested) {
  if (Test-FreshCommunicationPreflight -Feature "discussions") {
    $discussionsOutput = & gh repo edit github.com/OWNER/REPO --enable-discussions 2>&1
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Discussions could not be enabled; discussion-dependent output will be omitted. $($discussionsOutput | Out-String)"
    }
  }
}

$featureStateOutput = gh api --hostname github.com repos/OWNER/REPO
if ($LASTEXITCODE -ne 0) {
  throw "Could not verify repository communication features; omit dependent output."
}
$featureState = $featureStateOutput | ConvertFrom-Json
Assert-SelectedRepositoryId -RepositoryId $featureState.id
if ($featureState.full_name -isnot [string] -or
    -not [StringComparer]::OrdinalIgnoreCase.Equals($featureState.full_name, "OWNER/REPO") -or
    $featureState.archived -isnot [bool] -or $featureState.disabled -isnot [bool] -or
    $featureState.has_issues -isnot [bool] -or $featureState.has_discussions -isnot [bool]) {
  throw "Communication-feature readback is malformed or unbound; omit dependent output."
}
if ($featureState.archived -or $featureState.disabled) { throw "Repository is inactive; skip remote configuration." }
$hasIssuesEnabled = $featureState.has_issues
$hasDiscussionsEnabled = $featureState.has_discussions
```

Use only `$hasIssuesEnabled` and `$hasDiscussionsEnabled` from that final query when rendering templates and links. If a feature remains disabled, omit its dependent output instead of shipping dead navigation. For a local-only repository, use confirmed non-GitHub contacts until a remote exists; intended future state is not an enabled capability.

## Workflow installation preflight

Before copying a GitHub Actions asset, run the bundled read-only preflight. It
binds the response to the exact repository, rejects archived or disabled
repositories, bounds the workflow input count and total bytes, and checks
whether GitHub Actions is enabled. Pass
`--require-external-actions` for any asset with `uses:`. A `local_only` policy
forbids those assets, including Docker container actions. For a `selected`
policy, pass each candidate asset with
`--workflow`; the preflight follows the `selected_actions_url` advertised by
GitHub, including organization or enterprise policy overrides, and checks every
exact pinned action reference. It fails closed unless each reference matches an
explicit allowlist pattern or is covered by GitHub's `actions/*` or
`github/*` allowance. Negative allowlist patterns remain blocking. This preflight
accepts pattern matches only for public repositories,
because it does not infer Enterprise Cloud eligibility. Marketplace verified-creator
access alone is not treated as proof for a specific action. When an asset is
provided through `--workflow`, the preflight derives its external-action and
shipped issue-workflow requirements. `--require-issues` remains an explicit
assertion for an issue-writing workflow outside the shipped asset names.
If a supplied workflow calls a local reusable workflow, pass the called
workflow as another `--workflow` input in the same invocation; unresolved local
calls are rejected before the action policy can be approved. The called input
must come from the same workflow directory as its caller; a matching basename
from another directory is ambiguous and is rejected. The supplied called
workflow must declare `workflow_call`, and local call loops are rejected.
The supplied local call graph must also stay within GitHub's 10 workflow levels
and 50 unique nested workflows per top-level caller.
Any supplied workflow that declares `pull_request_target` needs a separate
event-policy review for public repositories. GitHub's default policy is scheduled
to block that event on November 2, 2026 unless an applicable workflow-execution
policy explicitly allows it. Inspect the applicable inherited policies with the
read-only [Actions policies API](https://docs.github.com/en/rest/actions/policies).
The workflow-installation preflight performs this check and requires an active
`restrict_action_events` rule that includes `pull_request_target` for every
supplied workflow path. Defer installation when that proof is unavailable. The
selected-actions result does not establish this event-policy proof, and the
plugin does not create or modify the remote policy.
For a project with a runnable test or lint command, the workflow phase must
install a configured CI workflow or record an explicit user decision to defer
it before the scaffold is declared complete. For every applicable approved
asset, pass the exact asset paths through `--workflow`, copy the required
companion files, and verify the installed files. Optional assets remain
feature-specific, but omission must be recorded as not applicable or deferred.
For any supplied `pull_request` workflow with a write permission, it also
requires `--confirm-pull-request-write-tokens`. Treat this as an explicit
operator assertion about the intended event, actor and target, not proof from
workflow YAML alone. Verify effective token scopes and applicable repository,
organization and enterprise policy before confirming. The **Send write tokens
to workflows from pull requests** setting applies to fork PRs of private
repositories; it is not a prerequisite for every same-repository PR and must
not be enabled merely to install an asset. Public external-fork tokens remain
restricted. Same-repository Dependabot automation can request explicit scopes,
as shown in [GitHub's automation guidance](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/automate-dependabot-with-actions).
The shipped asset never executes PR code and acts only on same-repository
Dependabot PRs. Review that exact actor/event path and retain its limited scopes;
do not substitute `pull_request_target` or a broad token-policy change.
If effective permission proof is unavailable, leave the assertion false and
defer installation. Boolean validation is not evidence of approval provenance
or effective permission, and this preflight never changes token policy.

```powershell
$workflowPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/workflow_installation_preflight.py"
if (-not (Test-Path -LiteralPath $workflowPreflight -PathType Leaf)) {
  throw "The bundled workflow-installation preflight is missing; do not copy workflow assets."
}
$workflowPreflightArguments = @(
  "--repository", "OWNER/REPO",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--hostname", "github.com",
  "--require-external-actions",
  "--workflow", "assets/workflows/ci.yml"
)
# For an issue-writing workflow that does not contain either marker, set this true.
# Every --workflow input containing issues: write or permissions: write-all is
# detected and cannot bypass the Issues check when this stays false.
$requiresIssueOperations = $false
if ($requiresIssueOperations -isnot [bool]) {
  throw "Issue-operation assertion must be a Boolean."
}
if ($requiresIssueOperations) { $workflowPreflightArguments += "--require-issues" }
$pullRequestWriteTokensConfirmed = $false
# Set only after verifying effective token scopes for the intended actor/event and target policy.
if ($pullRequestWriteTokensConfirmed -isnot [bool]) {
  throw "Pull-request write-token assertion must be a Boolean."
}
if ($pullRequestWriteTokensConfirmed) {
  $workflowPreflightArguments += "--confirm-pull-request-write-tokens"
}
$workflowPreflightOutput = python $workflowPreflight @workflowPreflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Workflow-installation inspection is inconclusive; do not copy the asset. $($workflowPreflightOutput | Out-String)"
}
$workflowPreflightText = $workflowPreflightOutput | Out-String
if (-not $workflowPreflightText.TrimStart().StartsWith("{")) { throw "Workflow preflight must be an object." }
$workflowPreflightResult = $workflowPreflightText | ConvertFrom-Json -ErrorAction Stop
Assert-SelectedRepositoryId -RepositoryId $workflowPreflightResult.repository_id
if ($workflowPreflightResult -isnot [pscustomobject] -or
    $workflowPreflightResult.repository -isnot [string] -or
    $workflowPreflightResult.decision -isnot [string] -or
    $workflowPreflightResult.inspection_complete -isnot [bool] -or
    -not $workflowPreflightResult.inspection_complete -or
    $workflowPreflightResult.decision -ne "may-install-workflow-assets") {
  throw "Workflow capability is not confirmed. Resolve the returned decision and rerun before copying the asset."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$workflowPreflightResult.repository, "OWNER/REPO"
)) {
  throw "Workflow-installation preflight returned a different repository; do not copy assets."
}
```

The workflow verdict must remain bound to the original discovery ID. A matching
name is not enough, and an unbound discovery-only decision cannot authorize
copying any asset. Multi-request inspection re-reads the repository controlling
state, Actions permissions, any selected-actions policy and any inherited
event-policy snapshot before returning the verdict. Numeric repository-scoped
selected-actions URLs must match the target ID; organization/enterprise
overrides still use the validated advertised URL. Drift or an unavailable
revalidation is inconclusive, not permission to use a cached verdict. Run the
inspection again immediately before each separate asset copy; this does not
claim atomic API/file-copy guarantees after the final read.

When installing `code-scanning-gate.yml`, pass both the gate and
`freshness.yml` as `--workflow` inputs in the same preflight invocation, then
pass `assets/code-scanning-allowlist.json` with `--code-scanning-allowlist`.
The preflight rejects a gate plan without both companions, so copy all three
verified inputs only after it returns `may-install-workflow-assets`. The supplied
allowlist must contain only schema-v3 entries with exact selector fields, unique
positive alert numbers, canonical POSIX paths without traversal or control
characters, non-future ISO review dates, and
review periods from 1 to 366 days. Its top-level fields must be exactly
`schema-version` and `allowlist`; malformed entries fail before approval. The
freshness workflow must use only scheduled and manual triggers. Each schedule
entry must use a five-field POSIX cron expression with an optional valid IANA
timezone, and `workflow_dispatch` must
be empty or a valid input mapping. A configured manual trigger may contain at
most 25 named input mappings, using only supported fields and input types. It
must request only `contents: read` and `issues: write` permissions, execute the
freshness checker,
and reconcile a marker issue
through a real repo-bound `gh issue create` or `gh issue edit --repo ...
--body-file` command. Every `create` or `edit` mutation must use
`--body-file`, `create` must provide a non-empty `--title`, and any `gh issue
close` mutation must also use an explicit `--repo` binding. Its concurrency
group must be the stable `repo-scaffold-freshness-${{ github.repository }}`
repository-scoped, non-cancelling group so a manual run on another ref cannot
race the scheduled run. Since `workflow_dispatch` can target a branch or tag,
its Issue-writing job must check out the exact
`${{ github.event.repository.default_branch }}` ref with
`persist-credentials: false` before running repository code. Untrusted triggers,
comments, shell-ambiguous commands, or an incomplete reminder do not satisfy the companion
requirement. If the reconciliation job declares job-level permissions, it must
retain effective `contents: read` and `issues: write` access so it can check
out and reconcile the repository. The `--body-file` value must match the
audit's Markdown output in the same job. Direct REST mutations through
`gh api`, including body-bearing default-`POST` calls, state-changing calls
through known direct HTTP clients
(`curl`, `wget`, and PowerShell REST cmdlets), path-qualified `gh` executables,
and shell wrappers, aliases, or function definitions that can hide or shadow
GitHub commands are rejected.
Changes to command lookup through `PATH`, `BASH_ENV`, `ENV`, or the shell's
command hash are also rejected.
Every freshness API lookup and Issue mutation must bind directly to the runner's
`$GITHUB_REPOSITORY` value, with `github.com/` explicit for `gh issue --repo`;
hard-coded repositories and overrides of that variable are rejected. The lookup
must use the GitHub Issue Search API as a bounded GET for open Issues, with
`is:issue`, `in:body`, the freshness marker, and `per_page=2`; it returns at most
the first two matching issue numbers so reruns remain idempotent without an
unbounded pagination loop. Use the shipped checked projection, not an
unconditional `[.items[].number] | join(" ")`: first require explicit Boolean
`incomplete_results: false`, a non-negative integer `total_count`, and exactly
`min(total_count, 2)` items with unique positive integer issue numbers. Partial,
missing, malformed, or inconsistent evidence must fail before mutation. The
checked result is one shell-safe line; verified zero matches emit `none`, not
empty stdout. Require the canonical empty-output failure guard immediately
after lookup and before issue-array initialization. Skip collection only for
`none`; never pass the sentinel as an Issue argument. This keeps an unexpected
bodyless CLI success from masquerading as verified evidence.
Retain exactly one lookup and no extra
`gh api` arguments. A checked line-oriented projection must use its matching
`mapfile` collector. The installation gate enforces this even for a standalone
repo-scaffold reminder without a code-scanning gate.
Do not seed or reuse issue lookup/array state before the unconditional lookup.
After `set -euo pipefail`, only marker/title setup and the reviewed read-only
marker check may precede lookup. A declared freshness workflow that fails its
executable-lifecycle inspection is invalid, not an absent optional companion;
do not install it from that verdict.
The lookup result must be captured and flow into the Issue number passed to a
`close` or `edit` mutation, directly or through an issue-number array; logging
or testing the result alone is insufficient.
The reconciliation shell must start with `set -euo pipefail` and may not later
disable any of those options.
The reconciliation job and its steps may not use `if`, `needs`, `strategy`,
`environment`, `concurrency`, `snapshot`, `cache-mode`, or
`continue-on-error`; steps also may not use `background`, `parallel`, `wait`,
`wait-all`, `cancel`, or `timeout-minutes`, which could silently skip, duplicate,
or mask the reminder.
The checker exit status must drive the clean/stale split: close the existing
issue when clean, and edit or create the report when stale before failing.
The community-health checker accepts only exit statuses 0, 1, and 2; any other
status must fail closed before clean/stale branching or Issue mutation.
`CHECKER_EXIT` must be bound to the audit step's `checker_exit` output, and the
audit output must derive from the checker's exit status.
The audit must disable `errexit` while running the checker, capture its status,
and restore `errexit` before publishing that output.
The JSON and Markdown outputs must be exactly
`$RUNNER_TEMP/freshness.json` and `$RUNNER_TEMP/freshness.md`; the reconciliation
must validate that same marker with `grep` before branching on checker status or
mutating an Issue, and reject multiple open marker issues.
The `close` and `edit` issue argument must be the unmodified lookup result or
its first array element; shell defaults and parameter transformations are
rejected.
The audit step's `GITHUB_TOKEN` and reconciliation step's `GH_TOKEN` must both
bind to `${{ github.token }}`; runner output and temporary-report paths may not
be overridden, including through shell assignments or parameter-expansion writes.
`PYTHONPATH`, `PYTHONHOME`,
and `PYTHONSTARTUP` may not be supplied to the checker. Shell assignments to
process and GitHub CLI configuration variables such as `GIT_SSH_COMMAND`,
`GH_CONFIG_DIR`, `HOME`, and `LD_PRELOAD` are also rejected. No other workflow,
job, or step environment variables may be supplied.
GitHub expressions are rejected inside freshness `run` blocks; only the exact
YAML token bindings and repository-scoped concurrency expressions are allowed.
The bound `GITHUB_TOKEN` and `GH_TOKEN` must not be referenced from a freshness
`run` block; `gh` must inherit them only through the exact YAML bindings.
The reminder job must run on `ubuntu-latest` with Bash as its effective shell;
non-Bash runner or shell overrides, workflow/job containers, and services are
rejected. Its reviewed checkout and Python setup actions must retain the
canonical full-SHA references and inputs, including the exact default-branch
checkout ref and `persist-credentials: false`, plus `python-version: 3.x`.
Both preparation actions must appear exactly once,
before any run step, so the checker has its repository files and Python runtime.
Any
repository, path, token, cache, or other input override is rejected. An
arbitrary or omitted checkout ref is also rejected for manually dispatched
Issue-writing workflows.
The job summary must publish only the checked Markdown report with
`cat "$RUNNER_TEMP/freshness.md" >> "$GITHUB_STEP_SUMMARY"`.
Within the reconciliation job, the audit must complete before the lookup, and
the lookup must complete before any Issue mutation. Pipeline, background, and
short-circuit operators (`|`, `&`, `|&`, `&&`, and `||`) are rejected around
these phases.
Shell negation (`!`) is also rejected in freshness commands.
An optional, single literal `--language en` or `--language vi` audit argument
selects human-facing report text. Use the reviewed workflow display translations
in `scaffold-generation.md`; do not translate protocol values, technical
diagnostics, markers, job identifiers, or shell guards. Unsupported and duplicate
language arguments fail closed, and language cannot override a path or token.
The audit must run from the checkout root with `--repository-root .`; if a
`--tracker-registry` override is present, it must name
`.github/freshness-trackers.json`. Directory-changing commands and workflow,
job, or step `working-directory` overrides are rejected.
Job-level reusable-workflow calls are also rejected so every freshness command
and Issue mutation remains directly inspectable in the supplied workflow.
  Only the canonical freshness command set is permitted in the reconciliation
  job, including only the reviewed `printf` invocations; unreviewed executables,
  script interpreters, command substitutions, and
  path-qualified programs are rejected. Issue mutations may use only their
  reviewed `--repo`, `--comment`, `--title`, and `--body-file` options; extra
  mutation flags and unreviewed exit statuses are rejected. `printf` formats
  must be literal and may not use shell expansion, `%n`, or `-v`. Local title
  and issue number state must use the canonical initialization and cannot be
  reseeded or reordered.
The checked-in tracker registry must retain every shipped workflow, release,
allowlist, and requirement input; do not empty a category to suppress a check.
Its version-1 schema supports only known top-level fields and known
requirement-source fields; lock paths cannot reference requirement sources, so
new inputs cannot be silently ignored.
The freshness checker bounds each tracked workflow read to 5 MiB and records an
indeterminate check when a file exceeds that cap. It resolves independent
upstream inputs with a bounded worker pool, preserving deterministic findings and
fail-closed errors. It also caps the tracked workflow inventory at 500 files and
64 MiB, and the distinct action repositories it resolves at 500, so large or
hostile repositories cannot force unbounded local reads or upstream lookups.
Requirements and tracked JSON policy inputs are bounded to 1 MiB before parsing.
The tracker registry is also capped at
500 tracked input paths, including requirement locks. Each requirements file
may contain at most 512 unique direct pins, and all tracked requirement sources
and locks together at most 4096 pins.
The reconciliation job itself
must inherit or declare
`issues: write`, be named `freshness-audit`, and use `timeout-minutes: 15`; a
grant on another job is insufficient.

## Inherited community-health policy

Before proposing local community files, inspect defaults inherited from the account's **public** `.github` repository. GitHub.com does not apply account defaults from an internal or private `.github` repository. Local files override defaults, and any local `.github/ISSUE_TEMPLATE` file disables the inherited issue-template directory as a set.

```powershell
$targetOutput = gh repo view github.com/OWNER/REPO --json name,owner,isFork
if ($LASTEXITCODE -ne 0) { throw "Failed to read the target repository." }
$target = $targetOutput | ConvertFrom-Json
$owner = $target.owner.login
$repo = $target.name

if (-not (Get-Variable REPO_ROOT -ErrorAction SilentlyContinue)) {
  throw "REPO_ROOT must be the surveyed target repository root before checking inherited policy."
}
$repoRootFullPath = [IO.Path]::GetFullPath($REPO_ROOT)
if (-not (Test-Path -LiteralPath $repoRootFullPath -PathType Container)) {
  throw "REPO_ROOT is not an existing directory."
}

function Assert-NotRepositoryLink([System.IO.FileSystemInfo]$Item) {
  $linkTypeProperty = $Item.PSObject.Properties["LinkType"]
  $hasLinkType = $null -ne $linkTypeProperty -and
    -not [string]::IsNullOrWhiteSpace([string]$linkTypeProperty.Value)
  $isReparsePoint = ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
  if ($hasLinkType -or $isReparsePoint) {
    throw "Repository survey refuses linked or reparse-point path '$($Item.FullName)'."
  }
}

$repoRootItem = Get-Item -LiteralPath $repoRootFullPath -Force
Assert-NotRepositoryLink $repoRootItem
$repoRootTrimmed = $repoRootFullPath.TrimEnd(
  [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
)
$repoRootBoundary = $repoRootTrimmed + [IO.Path]::DirectorySeparatorChar
$pathComparison = if (
  [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
) {
  [StringComparison]::OrdinalIgnoreCase
} else {
  [StringComparison]::Ordinal
}

function Assert-RepositorySurveyPath([string]$Candidate) {
  $candidateFullPath = [IO.Path]::GetFullPath($Candidate)
  $insideRoot = $candidateFullPath.Equals(
    $repoRootFullPath,
    $pathComparison
  ) -or $candidateFullPath.StartsWith(
    $repoRootBoundary,
    $pathComparison
  )
  if (-not $insideRoot) {
    throw "Repository survey path escapes REPO_ROOT: '$candidateFullPath'."
  }

  $currentPath = $repoRootFullPath
  if (-not $candidateFullPath.Equals(
    $repoRootFullPath,
    $pathComparison
  )) {
    $relativePath = $candidateFullPath.Substring($repoRootBoundary.Length)
    foreach ($component in $relativePath.Split(
      [char[]]@([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar),
      [StringSplitOptions]::RemoveEmptyEntries
    )) {
      $currentPath = Join-Path $currentPath $component
      if (-not (Test-Path -LiteralPath $currentPath)) { break }
      Assert-NotRepositoryLink (Get-Item -LiteralPath $currentPath -Force)
    }
  }
  return $candidateFullPath
}

function Get-RepositorySurveyFile([string]$Directory, [switch]$Recurse) {
  $safeDirectory = Assert-RepositorySurveyPath $Directory
  if (-not (Test-Path -LiteralPath $safeDirectory)) { return @() }
  if (-not (Test-Path -LiteralPath $safeDirectory -PathType Container)) {
    throw "Repository survey directory is not a directory: '$safeDirectory'."
  }

  $pending = [Collections.Generic.Stack[string]]::new()
  $files = [Collections.Generic.List[System.IO.FileInfo]]::new()
  $pending.Push($safeDirectory)
  while ($pending.Count -gt 0) {
    $currentDirectory = $pending.Pop()
    foreach ($item in @(Get-ChildItem -LiteralPath $currentDirectory -Force)) {
      Assert-NotRepositoryLink $item
      if ($item.PSIsContainer) {
        if ($Recurse) { $pending.Push($item.FullName) }
      } else {
        $files.Add($item)
      }
    }
  }
  return $files.ToArray()
}

$localIssueTemplateDirectory = Assert-RepositorySurveyPath (
  Join-Path $repoRootFullPath ".github/ISSUE_TEMPLATE"
)
$localIssueTemplateFiles = @()
if (Test-Path -LiteralPath $localIssueTemplateDirectory -PathType Container) {
  $localIssueTemplateFiles = @(
    Get-RepositorySurveyFile -Directory $localIssueTemplateDirectory -Recurse
  )
}
$localPullRequestTemplateFiles = @()
foreach ($directory in @(".github", ".", "docs")) {
  $directoryPath = Assert-RepositorySurveyPath (Join-Path $repoRootFullPath $directory)
  if (Test-Path -LiteralPath $directoryPath -PathType Container) {
    $localPullRequestTemplateFiles += @(
      Get-RepositorySurveyFile -Directory $directoryPath | Where-Object {
        $_.Name -match '(?i)^PULL_REQUEST_TEMPLATE(?:\..+)?$'
      }
    )
  }
}
foreach ($directory in @(
  ".github/PULL_REQUEST_TEMPLATE",
  "PULL_REQUEST_TEMPLATE",
  "docs/PULL_REQUEST_TEMPLATE"
)) {
  $directoryPath = Assert-RepositorySurveyPath (Join-Path $repoRootFullPath $directory)
  if (Test-Path -LiteralPath $directoryPath -PathType Container) {
    $localPullRequestTemplateFiles += @(
      Get-RepositorySurveyFile -Directory $directoryPath -Recurse
    )
  }
}

# The profile exposes effective community files for non-forks. Keep the full
# files object so source URLs can reveal an inherited OWNER/.github policy.
$effectiveCommunityFiles = $null
if (-not $target.isFork) {
  $profileOutput = gh api --hostname github.com "repos/$owner/$repo/community/profile" 2>&1
  if ($LASTEXITCODE -eq 0) {
    $effectiveCommunityFiles = (($profileOutput | Out-String) | ConvertFrom-Json).files
  } else {
    Write-Warning "Could not inspect effective community-profile files; do not claim that no inherited policy exists. $($profileOutput | Out-String)"
  }
}

$inheritedPaths = @()
$defaults = $null
if ($repo -ne ".github") {
  $defaultsOutput = gh repo view "github.com/$owner/.github" --json visibility,defaultBranchRef 2>&1
  if ($LASTEXITCODE -eq 0) {
    $defaults = ($defaultsOutput | Out-String) | ConvertFrom-Json
    if ($defaults.visibility -eq "PUBLIC" -and $null -ne $defaults.defaultBranchRef) {
      $encodedDefaultsBranch = [Uri]::EscapeDataString($defaults.defaultBranchRef.name)
      $treeOutput = gh api --hostname github.com `
        "repos/$owner/.github/git/trees/${encodedDefaultsBranch}?recursive=1" 2>&1
      if ($LASTEXITCODE -eq 0) {
        $defaultsTree = ($treeOutput | Out-String) | ConvertFrom-Json
        if ($defaultsTree.truncated) {
          Write-Warning "The $owner/.github tree response was truncated; inspect supported paths directly before claiming no inherited policy exists."
        }
        $inheritedPaths = @(
          $defaultsTree.tree | Where-Object type -eq "blob" | ForEach-Object { $_.path }
        )
      } else {
        Write-Warning "The $($defaults.visibility.ToLowerInvariant()) $owner/.github repository exists, but its default files could not be inspected. $($treeOutput | Out-String)"
      }
    }
  } elseif (($defaultsOutput | Out-String) -notmatch '(?is)HTTP 404|Could not resolve to a Repository') {
    Write-Warning "Could not determine whether $owner/.github supplies inherited policy; do not claim that none exists. $($defaultsOutput | Out-String)"
  }
}

$inheritedPathSet = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($path in $inheritedPaths) { [void]$inheritedPathSet.Add($path.Replace('\', '/')) }

$inheritedDefaultFiles = [ordered]@{}
foreach ($name in @(
  "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "FUNDING.yml", "GOVERNANCE.md",
  "SECURITY.md", "SUPPORT.md"
)) {
  foreach ($candidate in @(".github/$name", $name, "docs/$name")) {
    if ($inheritedPathSet.Contains($candidate)) {
      $inheritedDefaultFiles[$name] = $candidate
      break
    }
  }
}
$defaultsArePublic = $null -ne $defaults -and $defaults.visibility -eq "PUBLIC"
if ($defaultsArePublic) {
  foreach ($prefix in @(".github/", "", "docs/")) {
    $candidate = $inheritedPaths | Where-Object {
      $_ -match "(?i)^$([regex]::Escape($prefix))PULL_REQUEST_TEMPLATE(?:\..+)?$"
    } | Select-Object -First 1
    if ($null -ne $candidate) {
      $inheritedDefaultFiles["PULL_REQUEST_TEMPLATE"] = $candidate
      break
    }
  }
}
$availableDefaultIssueTemplates = @(
  if ($defaultsArePublic) {
    $inheritedPaths | Where-Object { $_ -match '(?i)^\.github/ISSUE_TEMPLATE/.+' }
  }
)
$inheritedIssueTemplates = if ($localIssueTemplateFiles.Count -eq 0) {
  @($availableDefaultIssueTemplates)
} else {
  @()
}
$availableDefaultPullRequestTemplates = @(
  if ($inheritedDefaultFiles.Contains("PULL_REQUEST_TEMPLATE")) {
    $inheritedDefaultFiles["PULL_REQUEST_TEMPLATE"]
  }
  if ($defaultsArePublic) {
    $inheritedPaths | Where-Object {
      $_ -match '(?i)^(?:\.github/|docs/)?PULL_REQUEST_TEMPLATE/.+'
    }
  }
)
$inheritedPullRequestTemplates = if ($localPullRequestTemplateFiles.Count -eq 0) {
  @($availableDefaultPullRequestTemplates)
} else {
  # A local single or multi-template source suppresses account-default PR templates.
  [void]$inheritedDefaultFiles.Remove("PULL_REQUEST_TEMPLATE")
  @()
}
$inheritedDiscussionForms = @(
  $inheritedPaths | Where-Object { $_ -match '(?i)^\.github/DISCUSSION_TEMPLATE/.+' }
)
```

Compare the effective profile URLs with the target repository and `$owner/.github`, and report `$inheritedDefaultFiles`, `$inheritedIssueTemplates`, `$inheritedPullRequestTemplates`, and `$inheritedDiscussionForms` separately from local sources. Treat `$availableDefaultIssueTemplates` and `$availableDefaultPullRequestTemplates` as account defaults only, not effective inheritance: when the corresponding local template list is non-empty, report why those account defaults are suppressed and evaluate the local source by itself. If a target file is absent locally but effectively inherited, ask before creating the local override; approval to scaffold files generally is not approval to replace an account-wide policy.

When `$inheritedIssueTemplates` is non-empty, fetch each effective template from `$owner/.github` and parse its metadata before configuring labels. For issue forms, parse the YAML `labels` field; for legacy Markdown templates, parse only the YAML front matter and normalize its `labels` value. Do not scrape labels from template body text. Record the resulting label names as `$effectiveInheritedIssueLabels`. A referenced label works only when it exists in both `$owner/.github` and the target repository. Report an account-default label missing from `$owner/.github` as a broken default; do not mutate that separate repository without explicit confirmation. For a label present in `$owner/.github` but absent from the target, copy its confirmed name, color, and description to the target after confirmation. Never invent label metadata or claim the inherited template is fully functional until both repositories have been verified.

## Branch protection (classic)

Enable only when the user wants to enforce the PR flow. Require a PR, passing selected checks, up-to-date branches, and apply to admins too. A solo owner can still self-merge (0 required approvals). Use the detected default branch; never substitute a fixed branch name.

On GitHub.com, protected branches are available for private repositories only with GitHub Pro, Team, or Enterprise Cloud. For a private/internal repository, confirm entitlement before mutation when possible. Otherwise treat `403` as forbidden and preserve `404` as a missing-or-inaccessible repository/branch result unless separate evidence proves a plan limitation. Continue the scaffold without claiming protection was enabled.

Build the check list from contexts verified during the scaffold run, not from workflow filenames:

- Add `ci-success` only when the repo-scaffold CI asset was created or an existing CI workflow was approved for update and verified to emit that aggregate check.
- Add `dependency-review` only when the matching repo-scaffold asset was created for an eligible repository.
- Add `commitlint` only when the matching repo-scaffold workflow asset was created.
- For an unchanged existing workflow, inspect its `jobs` mapping and optional job-level `name`, then ask the user which real aggregate context to require. A matching filename is not evidence that a particular check exists.
- Compute the producers of every candidate required context across the final workflow set before mutation: use job-level `name` when present, otherwise the job ID, and resolve matrix/reusable-workflow names to the check context GitHub actually emits. GitHub does not scope required checks by workflow, matrix, or event, so each required context must have exactly one producer.
- Verify event coverage, not only the context name. A required producer must run for every `pull_request` without workflow-level `paths`, `paths-ignore`, or branch filters that can suppress the entire workflow. When an effective merge queue applies, it must also run for `merge_group` with `checks_requested`. Any job-level `if` must evaluate true and execute the real validation for every relevant event; GitHub reports skipped jobs as successful, which is not evidence that the gate ran. Duplicate names unrelated to the required set must not block protection.
- For a generated repo-scaffold asset, parse the final YAML and record this coverage directly. For an unchanged, dynamic, matrix-named, reusable, or externally supplied check, verify a representative PR run (and a merge-group run when applicable) or do not require it.
- For every representative PR, retrieve both `.head.sha` and the current `.merge_commit_sha` with `gh api --hostname github.com repos/OWNER/REPO/pulls/NUMBER --jq '{head_sha: .head.sha, test_merge_sha: .merge_commit_sha}'`. Require a mergeable representative PR with a non-null test-merge SHA. Inspect complete paginated results from Check Runs (`gh api --hostname github.com --paginate repos/OWNER/REPO/commits/SHA/check-runs`) and Commit Statuses (`gh api --hostname github.com --paginate repos/OWNER/REPO/commits/SHA/statuses`) on both SHAs, plus a merge-group SHA when applicable and recent default-branch SHAs. If the test-merge SHA has any Check Runs or Commit Statuses, it controls: require the selected context and source on that SHA, and do not fall back to a passing head-only result. If it has neither, use the head SHA. A check must have completed successfully in this repository during the past seven days on the controlling SHA before it can be selected as required. Compare context names case-insensitively. GitHub requires both systems when a Check Run and Commit Status share a required name, so reject that candidate on a controlling SHA instead of treating it as one producer. Record the intended Check Run's exact positive `app.id` from the controlling SHA; when checking multiple representative PRs, require their controlling-sha app IDs to agree. Bind every new required check to that verified app ID rather than allowing GitHub to auto-select a recent source.
- When an effective merge queue applies, also pass a recent successful `merge_group` SHA to the preflight. It checks that the same workflow blob and context execute on that SHA, the Actions run's event is exactly `merge_group`, and its GitHub App ID matches the selected PR check. No verified merge-group SHA means no branch-protection mutation.
- Stop before applying required-status-check protection when no real gate has been confirmed. Never submit a context that no workflow emits.

The structural producer check rejects conditional steps and `continue-on-error`.
Its only step-condition exception is the shipped checkout guard that skips an
explicitly closed PR. That literal condition executes checkout for every open
PR and merge group, must use pinned `actions/checkout` with the event's exact
head SHA and disabled credential persistence, and cannot guard a `run` step.
Actor filters, inverted state conditions and unverified checkout refs remain
inconclusive. Closed-PR not-applicable results do not certify an open PR gate.

Select the exact required contexts before running the preflight. Carry that same
list and its returned App IDs through to the mutation; do not rebuild the list
or enter producer evidence again afterwards.

The bundled `scripts/branch_protection_preflight.py` turns this proof into a
read-only, fail-closed gate. Run it after the final workflows are pushed to a
open, mergeable representative PR and before any branch-protection mutation. It reads
the exact regular-file workflow blobs from the PR's test-merge commit. GitHub
runs `pull_request` workflows from that merge commit ([event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).
The preflight rejects duplicate YAML keys and ambiguous producers, verifies
unfiltered `pull_request` coverage (or a trusted
`pull_request_target` producer from the verified default branch) plus
`merge_group` coverage when an effective merge queue applies, requires an
unconditional executable job, and verifies a completed successful Check Run no
older than seven days on the controlling SHA: the test-merge SHA when it has any
Check Runs or Commit Statuses, otherwise the PR head SHA. It takes the GitHub App ID from
that same controlling SHA and rejects any same-name Commit Status collision
there. When a queue applies, it also requires a recent successful Check Run on
the supplied merge-group SHA with the same workflow blob, context, and App ID;
the Actions workflow run must report the exact `merge_group` event. It joins
each Check Run's `check_suite.id` to exactly one Actions workflow run and
verifies the run's commit, workflow path, and event. A successful
`workflow_dispatch` run cannot stand in for an eligible required-check event.
For fine-grained tokens, this workflow-run read requires the repository's
Actions: read permission; inaccessible or incomplete run evidence is
inconclusive.
For the shipped closed-PR lifecycle names, producer discovery resolves only
the literal fallback used by open PRs and merge groups. The closed noop has a
distinct non-required name and must not supply success for the normal context.
Other dynamic names remain unsupported. GitHub's `workflow_run.pull_requests`
field lists open matching heads/branches, not necessarily the PR that
triggered that run; do not treat it as trigger-payload or workflow-blob
attestation. Event-specific provenance beyond the existing path/SHA checks
must be established separately before claiming a particular PR was validated.
Workflow inspection must verify the bounded raw UTF-8 bytes against the
advertised GitHub SHA-1 blob ID before parsing YAML or declaring a producer.
Preserve exact bytes, including CRLF, BOM and Unicode; do not hash normalized
text. A mismatch or unsupported object ID is inconclusive. This content
binding is not attestation of the workflow definition actually executed.
Root Git-tree inventory must bound all entries before classification and
reject non-object entries or missing, empty or wrong-type paths before any
blob read. Such entries are unclassifiable, not evidence of an irrelevant
file. Known non-workflow paths may still be excluded by the direct-file policy.
The selected Check Run must explicitly report `status: completed` and
`conclusion: success`, with a genuine string `name` matching the required
context. Never stringify null, Boolean, numeric or collection names into a
context. A successful conclusion or completion timestamp alone
does not replace that state: missing, malformed, queued or in-progress receipts
remain inconclusive and cannot authorize protection.
For `pull_request_target`, it also requires the exact workflow blob to match at
the PR base commit because GitHub runs that event from the base repository's
default branch. Merge any new or changed target workflow before running this
preflight. It never modifies GitHub state. It also binds the exact repository/default branch,
rejects archived or disabled targets, and requires current administration
permission. Any API, parsing, pagination, mergeability, or evidence gap is
inconclusive and required-check mutation remains forbidden.

Pass the originally discovered numeric repository ID, not a newly reseeded ID.
The preflight binds the representative PR's base repository ID and number,
revalidates its controlling head/base/test-merge/state, effective rules and the
trusted default-branch commit when applicable, then rereads active/admin/branch
repository state before returning the verdict. Drift is inconclusive. Without
the discovery ID, inspection is discovery-only and cannot authorize protection.

Resolve `REPO_SCAFFOLD_SKILL_ROOT` to the installed/source directory that
contains this skill's `SKILL.md`, then run:

```powershell
$defaultBranch = $repoView.defaultBranchRef.name
if ([string]::IsNullOrWhiteSpace($defaultBranch)) {
  throw "The repository has no default branch; confirm one before configuring protection."
}
# Replace this example with every context selected above. For an unchanged
# workflow, add only the context the user confirmed from its real job.
$requiredCheckNames = @("ci-success")
$mergeGroupSha = $null # Set to the verified recent merge_group SHA when a queue applies.
if ($requiredCheckNames.Count -eq 0) {
  throw "No verified required-check context; stop before configuring protection."
}
$branchProtectionPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/branch_protection_preflight.py"
if (-not (Test-Path -LiteralPath $branchProtectionPreflight -PathType Leaf)) {
  throw "The bundled branch-protection preflight script is missing; do not mutate protection."
}
$preflightArguments = @(
  "--repository", "OWNER/REPO",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--default-branch", $defaultBranch,
  "--pull-request", "NUMBER"
)
foreach ($context in $requiredCheckNames) {
  $preflightArguments += @("--required-check", [string]$context)
}
if ($null -ne $mergeGroupSha) {
  $preflightArguments += @("--merge-group-sha", [string]$mergeGroupSha)
}
function Assert-RequiredCheckPreflightSchema {
  param([object]$Verdict)
  if ($Verdict -isnot [pscustomobject]) { throw "Required-check verdict must be a JSON object." }
  Assert-SelectedRepositoryId -RepositoryId $Verdict.repository_id
  foreach ($field in @("inspection_complete", "administration_permission", "merge_queue_required")) {
    if ($Verdict.$field -isnot [bool]) { throw "Required-check verdict has an invalid Boolean field." }
  }
  if (-not $Verdict.administration_permission) { throw "Administration permission is not confirmed." }
  foreach ($field in @("repository", "default_branch", "decision")) {
    if ($Verdict.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($Verdict.$field)) {
      throw "Required-check verdict has an invalid text field."
    }
  }
  if (($Verdict.pull_request -isnot [int] -and $Verdict.pull_request -isnot [long]) -or
      $Verdict.pull_request -le 0) { throw "Required-check verdict has an invalid PR number." }
  foreach ($field in @("head_sha", "test_merge_sha")) {
    if ($Verdict.$field -isnot [string] -or $Verdict.$field -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
      throw "Required-check verdict has an invalid controlling commit."
    }
  }
  if ($null -eq $Verdict.PSObject.Properties['merge_group_sha'] -or
      ($Verdict.merge_queue_required -and
       ($Verdict.merge_group_sha -isnot [string] -or $Verdict.merge_group_sha -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$')) -or
      (-not $Verdict.merge_queue_required -and $null -ne $Verdict.merge_group_sha)) {
    throw "Required-check verdict has an invalid queue/commit binding."
  }
  if ($Verdict.required_checks -isnot [array] -or $Verdict.required_checks.Count -lt 1 -or
      $Verdict.required_checks.Count -gt 50) { throw "Required-check inventory is missing or out of bounds." }
  $contexts = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach ($check in $Verdict.required_checks) {
    if ($check -isnot [pscustomobject] -or $check.context -isnot [string] -or
        $check.context.Length -lt 1 -or $check.context.Length -gt 256 -or
        $check.context -match '[\r\n\x00]' -or -not $contexts.Add($check.context) -or
        ($check.app_id -isnot [int] -and $check.app_id -isnot [long]) -or $check.app_id -le 0 -or
        $check.producer -isnot [string] -or [string]::IsNullOrWhiteSpace($check.producer)) {
      throw "Required-check context, App ID or producer is malformed or duplicated."
    }
  }
}

$preflightOutput = python $branchProtectionPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Required-check evidence is inconclusive; do not mutate protection. $($preflightOutput | Out-String)"
}
$requiredCheckPreflight = ($preflightOutput | Out-String) | ConvertFrom-Json
Assert-RequiredCheckPreflightSchema -Verdict $requiredCheckPreflight
if ($requiredCheckPreflight.inspection_complete -isnot [bool] -or
    -not $requiredCheckPreflight.inspection_complete -or
    $requiredCheckPreflight.decision -ne "may-configure-classic-protection" -or
    $requiredCheckPreflight.merge_queue_required -isnot [bool]) {
  throw "Required-check evidence is incomplete; do not mutate protection."
}
if ($requiredCheckPreflight.repository -cne "OWNER/REPO" -or
    $requiredCheckPreflight.default_branch -cne $defaultBranch -or
    $requiredCheckPreflight.merge_queue_required -ne ($null -ne $mergeGroupSha) -or
    $requiredCheckPreflight.merge_group_sha -cne $mergeGroupSha) {
  throw "Branch-protection preflight input does not match the protection mutation."
}
$verifiedChecks = @($requiredCheckPreflight.required_checks)
$verifiedCheckNames = @($verifiedChecks | ForEach-Object { [string]$_.context })
if ($verifiedCheckNames.Count -ne $requiredCheckNames.Count -or
    $null -ne (Compare-Object `
      -ReferenceObject @($requiredCheckNames | Sort-Object) `
      -DifferenceObject @($verifiedCheckNames | Sort-Object))) {
  throw "Required-check preflight contexts differ from the mutation plan; rerun with the exact context list."
}
# Use only these returned values in the mutation block. Do not add contexts or
# substitute app IDs manually after the preflight completes.
$requiredCheckNames = $verifiedCheckNames
$requiredAppIdsByContext = [Collections.Generic.Dictionary[string, int64]]::new(
  [StringComparer]::OrdinalIgnoreCase
)
foreach ($check in $verifiedChecks) {
  if ([string]::IsNullOrWhiteSpace([string]$check.context) -or
      [int64]$check.app_id -le 0) {
    throw "Required-check preflight returned an invalid context or App ID."
  }
  $requiredAppIdsByContext[[string]$check.context] = [int64]$check.app_id
}

function Assert-FreshRequiredCheckPreflight {
  $output = python $branchProtectionPreflight @preflightArguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Final required-check preflight is inconclusive; do not mutate protection. $($output | Out-String)"
  }
  try {
    $fresh = ($output | Out-String) | ConvertFrom-Json
  } catch {
    throw "Final required-check preflight returned invalid JSON; do not mutate protection."
  }
  Assert-RequiredCheckPreflightSchema -Verdict $fresh
  if ($fresh.inspection_complete -isnot [bool] -or
      -not $fresh.inspection_complete -or
      $fresh.decision -ne "may-configure-classic-protection" -or
      $fresh.repository -cne "OWNER/REPO" -or
      $fresh.default_branch -cne $defaultBranch -or
      [int]$fresh.pull_request -ne [int]$requiredCheckPreflight.pull_request -or
      ([string]$fresh.head_sha).ToLowerInvariant() -cne
        ([string]$requiredCheckPreflight.head_sha).ToLowerInvariant() -or
      ([string]$fresh.test_merge_sha).ToLowerInvariant() -cne
        ([string]$requiredCheckPreflight.test_merge_sha).ToLowerInvariant() -or
      $fresh.merge_queue_required -isnot [bool] -or
      $fresh.merge_queue_required -ne $requiredCheckPreflight.merge_queue_required -or
      $fresh.merge_group_sha -cne $requiredCheckPreflight.merge_group_sha) {
    throw "Representative pull-request evidence or target changed since preflight; review the fresh result before mutating protection."
  }
  $approvedBindings = @($verifiedChecks | Sort-Object context | ForEach-Object {
    [ordered]@{ context = [string]$_.context; app_id = [int64]$_.app_id; producer = [string]$_.producer }
  })
  $freshBindings = @($fresh.required_checks | Sort-Object context | ForEach-Object {
    [ordered]@{ context = [string]$_.context; app_id = [int64]$_.app_id; producer = [string]$_.producer }
  })
  if ((ConvertTo-Json -InputObject $approvedBindings -Compress -Depth 4) -cne
      (ConvertTo-Json -InputObject $freshBindings -Compress -Depth 4)) {
    throw "Required-check contexts or App IDs changed since preflight; review the fresh result before mutating protection."
  }
  return $fresh
}
```

Run it with every context passed to the later mutation block. Immediately before
the first protection write, the example reruns it and requires the same
repository, branch, pull-request head, test-merge SHA, contexts, and App IDs.
When multiple contexts are eligible, pass one `--required-check` argument for
each and retain the returned context/app-ID pairs exactly. If any evidence
changed, review the fresh result and rerun the flow before mutation. The
PowerShell example below explains the preservation and post-mutation
verification of classic-protection settings; do not bypass this preflight by
hand-populating its producer evidence.

PowerShell example:

```powershell
# Read repository metadata as command output. Never paste a detected branch into
# PowerShell source: valid branch names may contain `$`, `;`, quotes, or parentheses.
$repoViewOutput = gh repo view github.com/OWNER/REPO --json nameWithOwner,defaultBranchRef,visibility
if ($LASTEXITCODE -ne 0) { throw "Failed to read repository metadata." }
$repoView = $repoViewOutput | ConvertFrom-Json
$owner, $repo = $repoView.nameWithOwner -split '/', 2
$defaultBranch = $repoView.defaultBranchRef.name
if ([string]::IsNullOrWhiteSpace($defaultBranch)) {
  throw "The repository has no default branch; confirm one before configuring protection."
}
if ($repoView.nameWithOwner -cne $requiredCheckPreflight.repository -or
    $defaultBranch -cne $requiredCheckPreflight.default_branch) {
  throw "Repository identity or default branch changed after preflight; rerun before mutating protection."
}
$encodedBranch = [Uri]::EscapeDataString($defaultBranch)

# Required checks need merge-group coverage whenever an effective queue rule applies.
$effectiveRuleOutput = gh api --hostname github.com --paginate `
  "repos/$owner/$repo/rules/branches/$encodedBranch" `
  --jq '.[] | select(.type == "merge_queue") | .type' 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Could not determine whether a merge queue applies; stop before configuring required checks. $($effectiveRuleOutput | Out-String)"
}
$hasMergeQueue = @($effectiveRuleOutput | Where-Object { $_ -eq "merge_queue" }).Count -gt 0
if ($hasMergeQueue -ne [bool]$requiredCheckPreflight.merge_queue_required) {
  throw "Effective merge-queue rules changed after preflight; rerun before mutating protection."
}
foreach ($context in $requiredCheckNames) {
  if (-not $requiredAppIdsByContext.ContainsKey($context)) {
    throw "Required-check preflight did not return a verified App ID for $context."
  }
}

function ConvertTo-BranchProtectionAppId([object]$Value) {
  if ($null -eq $Value) { return -1L }
  if (($Value -is [int] -or $Value -is [long]) -and [int64]$Value -gt 0) {
    return [int64]$Value
  }
  throw "Branch-protection App ID must be a positive integer or null."
}

function Get-BranchProtectionReviewState([object]$Protection) {
  if ($null -eq $Protection -or $Protection -isnot [pscustomobject]) {
    throw "Branch-protection response is incomplete; stop before mutation."
  }
  $reviewProperty = $Protection.PSObject.Properties['required_pull_request_reviews']
  if ($null -eq $reviewProperty -or $null -eq $reviewProperty.Value) {
    return $null
  }
  if ($reviewProperty.Value -isnot [pscustomobject]) {
    throw "Pull-request review protection is malformed; stop before mutation."
  }
  return $reviewProperty.Value
}

function Assert-ClassicProtectionState(
  [object]$Protection,
  [Collections.Generic.IDictionary[string, int64]]$ExpectedAppIdsByContext,
  [object]$ExpectedReviewApprovalCount = $null
) {
  $problems = @()
  $null = Get-RequiredStatusCheckSnapshot $Protection
  if ($Protection.required_status_checks.strict -isnot [bool] -or
      -not $Protection.required_status_checks.strict) {
    $problems += "strict status checks are disabled"
  }
  if ($Protection.enforce_admins.enabled -isnot [bool] -or
      -not $Protection.enforce_admins.enabled) {
    $problems += "administrator enforcement is disabled"
  }
  $reviewState = Get-BranchProtectionReviewState $Protection
  if ($null -eq $reviewState) {
    $problems += "pull request protection is absent"
  } elseif ($null -ne $ExpectedReviewApprovalCount) {
    $actualReviewApprovalCount = $reviewState.required_approving_review_count
    if (($actualReviewApprovalCount -isnot [int] -and
        $actualReviewApprovalCount -isnot [long]) -or
        [int64]$actualReviewApprovalCount -ne [int64]$ExpectedReviewApprovalCount) {
      $problems += "pull request approval count differs from the requested state"
    }
  }

  $actualAppIdsByContext = [Collections.Specialized.OrderedDictionary]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($check in @($Protection.required_status_checks.checks)) {
    if ($null -eq $check -or [string]::IsNullOrWhiteSpace($check.context)) {
      continue
    }
    try {
      $appId = ConvertTo-BranchProtectionAppId $check.app_id
    } catch {
      $problems += "$($check.context) has an invalid app binding"
      continue
    }
    if ($actualAppIdsByContext.Contains($check.context) -and
        [int64]$actualAppIdsByContext[$check.context] -ne $appId) {
      $problems += "$($check.context) has conflicting app bindings"
    } else {
      $actualAppIdsByContext[$check.context] = $appId
    }
  }
  foreach ($context in @($Protection.required_status_checks.contexts)) {
    if (-not [string]::IsNullOrWhiteSpace($context) -and
        -not $actualAppIdsByContext.Contains($context)) {
      $actualAppIdsByContext[$context] = -1L
    }
  }

  foreach ($context in @($ExpectedAppIdsByContext.Keys)) {
    if (-not $actualAppIdsByContext.Contains($context)) {
      $problems += "$context is missing"
    } elseif ([int64]$actualAppIdsByContext[$context] -ne
        [int64]$ExpectedAppIdsByContext[$context]) {
      $problems += "$context has an unexpected app binding"
    }
  }
  foreach ($context in @($actualAppIdsByContext.Keys)) {
    if (-not $ExpectedAppIdsByContext.ContainsKey($context)) {
      $problems += "$context is unexpectedly required"
    }
  }
  if ($problems.Count -gt 0) {
    throw "Classic branch protection did not reach the requested state: $($problems -join '; ')."
  }
}

$protectionPath = "repos/$owner/$repo/branches/$encodedBranch/protection"

function Get-RequiredStatusCheckSnapshot([object]$Protection) {
  if ($null -eq $Protection -or $Protection -isnot [pscustomobject]) {
    throw "Branch-protection response is incomplete; stop before mutation."
  }
  $statusProperty = $Protection.PSObject.Properties['required_status_checks']
  # The top-level property is optional when no required status checks are set.
  if ($null -eq $statusProperty) { return "null" }
  $statusChecks = $statusProperty.Value
  if ($null -eq $statusChecks) { return "null" }
  if ($statusChecks -isnot [pscustomobject]) {
    throw "Required-status-check state is incomplete; stop before mutation."
  }
  # The GET schema makes strict optional; the PATCH below intentionally sets it.
  $strictProperty = $statusChecks.PSObject.Properties['strict']
  $strictSnapshot = $null
  if ($null -ne $strictProperty) {
    if ($strictProperty.Value -isnot [bool]) {
      throw "Required-status-check strictness is invalid; stop before mutation."
    }
    $strictSnapshot = [bool]$strictProperty.Value
  }
  $checksProperty = $statusChecks.PSObject.Properties['checks']
  $contextsProperty = $statusChecks.PSObject.Properties['contexts']
  if ($null -eq $checksProperty -or $checksProperty.Value -isnot [array] -or
      $null -eq $contextsProperty -or $contextsProperty.Value -isnot [array]) {
    throw "Required-status-check bindings are incomplete; stop before mutation."
  }

  $bindings = [Collections.Generic.Dictionary[string, int64]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($check in @($statusChecks.checks)) {
    if ($null -eq $check -or $check -isnot [pscustomobject] -or
        $null -eq $check.PSObject.Properties['context'] -or
        $null -eq $check.PSObject.Properties['app_id'] -or
        $check.context -isnot [string] -or
        [string]::IsNullOrWhiteSpace($check.context)) {
      throw "Required-status-check state contains an invalid context."
    }
    $appId = ConvertTo-BranchProtectionAppId $check.app_id
    if ($bindings.ContainsKey([string]$check.context) -and
        $bindings[[string]$check.context] -ne $appId) {
      throw "Required-status-check state contains conflicting App bindings."
    }
    $bindings[[string]$check.context] = $appId
  }
  foreach ($context in @($statusChecks.contexts)) {
    if ($context -isnot [string] -or [string]::IsNullOrWhiteSpace($context)) {
      throw "Required-status-check state contains an invalid context."
    }
    if (-not $bindings.ContainsKey($context)) { $bindings[$context] = -1L }
  }
  $normalizedChecks = @(
    $bindings.Keys | Sort-Object -CaseSensitive | ForEach-Object {
      [ordered]@{ context = $_.ToLowerInvariant(); app_id = [int64]$bindings[$_] }
    }
  )
  return ([ordered]@{
    strict = $strictSnapshot
    checks = $normalizedChecks
  } | ConvertTo-Json -Depth 5 -Compress)
}

$protectionOutput = gh api --hostname github.com $protectionPath -H "Accept: application/vnd.github+json" 2>&1
$protectionExitCode = $LASTEXITCODE

if ($protectionExitCode -eq 0) {
  $existingProtection = $protectionOutput | ConvertFrom-Json
  $initialStatusCheckState = Get-RequiredStatusCheckSnapshot $existingProtection
  $initialReviewProtection = Get-BranchProtectionReviewState $existingProtection
  $checksByContext = [System.Collections.Specialized.OrderedDictionary]::new(
    [System.StringComparer]::OrdinalIgnoreCase
  )
  $bindingProblems = @()

  # Preserve every existing required check, including any GitHub App binding.
  foreach ($check in @($existingProtection.required_status_checks.checks)) {
    if ($null -eq $check -or [string]::IsNullOrWhiteSpace($check.context)) { continue }
    $entry = @{ context = $check.context }
    # A response value of null means the check accepts any app. In update requests,
    # GitHub requires -1 to preserve that behavior; omitting app_id may auto-select
    # the app that most recently supplied the check.
    try {
      $entry.app_id = ConvertTo-BranchProtectionAppId $check.app_id
    } catch {
      $bindingProblems += "$($check.context)=invalid app binding"
      continue
    }
    if ($checksByContext.Contains($check.context) -and
        [int64]$checksByContext[$check.context].app_id -ne [int64]$entry.app_id) {
      $bindingProblems += "$($check.context)=multiple existing app bindings"
    } else {
      $checksByContext[$check.context] = $entry
    }
  }
  foreach ($context in @($existingProtection.required_status_checks.contexts)) {
    if (-not [string]::IsNullOrWhiteSpace($context) -and -not $checksByContext.Contains($context)) {
      $checksByContext[$context] = @{ context = $context; app_id = -1 }
    }
  }
  foreach ($context in $requiredCheckNames) {
    $expectedAppId = $requiredAppIdsByContext[$context]
    if ($checksByContext.Contains($context)) {
      if ([int64]$checksByContext[$context].app_id -ne $expectedAppId) {
        $bindingProblems += "${context}=existing binding does not match verified app $expectedAppId"
      }
    } else {
      $checksByContext[$context] = @{ context = $context; app_id = $expectedAppId }
    }
  }
  if ($bindingProblems.Count -gt 0) {
    throw "Existing required-check bindings conflict with verified producers; no protection was changed: $($bindingProblems -join ', ')."
  }

  $requiredCheckPreflight = Assert-FreshRequiredCheckPreflight
  $statusProtectionBeforeWriteOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Could not re-read branch protection immediately before updating required checks; no new write was sent. $($statusProtectionBeforeWriteOutput | Out-String)"
  }
  try {
    $statusProtectionBeforeWrite = ($statusProtectionBeforeWriteOutput | Out-String) | ConvertFrom-Json
  } catch {
    throw "Branch-protection re-read returned invalid JSON; no new write was sent."
  }
  if ((Get-RequiredStatusCheckSnapshot $statusProtectionBeforeWrite) -cne
      $initialStatusCheckState) {
    throw "Required status checks changed after the initial read; do not overwrite the concurrent policy. Rerun the full protection review."
  }
  $statusPayload = @{
    strict = $true
    checks = @($checksByContext.Values)
  } | ConvertTo-Json -Depth 6
  $completedProtectionUpdates = @()
  $protectionUpdateFailure = $null
  $expectedReviewApprovalCount = $null
  try {
    $statusUpdateOutput = $statusPayload | gh api --hostname github.com -X PATCH `
      "$protectionPath/required_status_checks" `
      -H "Accept: application/vnd.github+json" --input - 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to update required status checks. $($statusUpdateOutput | Out-String)"
    }
    $completedProtectionUpdates += "required_status_checks"

    $null = Assert-FreshRequiredCheckPreflight
    $adminStateOutput = gh api --hostname github.com `
      "$protectionPath/enforce_admins" -H "Accept: application/vnd.github+json" 2>&1
    if ($LASTEXITCODE -ne 0) {
      throw "Could not re-read admin enforcement before its write. $($adminStateOutput | Out-String)"
    }
    try { $adminState = ($adminStateOutput | Out-String) | ConvertFrom-Json } catch {
      throw "Admin-enforcement re-read returned invalid JSON."
    }
    if ($adminState.enabled -isnot [bool]) {
      throw "Admin-enforcement re-read is incomplete; do not mutate."
    }
    if (-not $adminState.enabled) {
      $adminUpdateOutput = gh api --hostname github.com -X POST `
        "$protectionPath/enforce_admins" -H "Accept: application/vnd.github+json" `
        --silent 2>&1
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to enable admin enforcement. $($adminUpdateOutput | Out-String)"
      }
      $completedProtectionUpdates += "enforce_admins"
    }

    # Enable the PR requirement only when it is absent. Never weaken an existing review policy.
    if ($null -eq $initialReviewProtection) {
      $null = Assert-FreshRequiredCheckPreflight
      $reviewProtectionBeforeWriteOutput = gh api --hostname github.com $protectionPath `
        -H "Accept: application/vnd.github+json" 2>&1
      if ($LASTEXITCODE -ne 0) {
        throw "Could not re-read pull request review protection before its write. $($reviewProtectionBeforeWriteOutput | Out-String)"
      }
      try {
        $reviewProtectionBeforeWrite = ($reviewProtectionBeforeWriteOutput | Out-String) | ConvertFrom-Json
      } catch {
        throw "Branch-protection re-read returned invalid JSON; do not mutate review protection."
      }
      $reviewProtectionBeforeWriteState =
        Get-BranchProtectionReviewState $reviewProtectionBeforeWrite
      if ($null -eq $reviewProtectionBeforeWriteState) {
        $reviewUpdateOutput = '{"required_approving_review_count":0}' | `
          gh api --hostname github.com -X PATCH `
            "$protectionPath/required_pull_request_reviews" `
            -H "Accept: application/vnd.github+json" --input - 2>&1
        if ($LASTEXITCODE -ne 0) {
          throw "Failed to enable pull request review protection. $($reviewUpdateOutput | Out-String)"
        }
        $expectedReviewApprovalCount = 0
        $completedProtectionUpdates += "required_pull_request_reviews"
      } else {
        Write-Output "A concurrent review policy appeared; preserve it without an update."
      }
    }
  } catch {
    $protectionUpdateFailure = $_.Exception.Message
  }

  # These REST subresources cannot be updated atomically. Always re-read the final
  # state so a later failure cannot be mistaken for an all-or-nothing result.
  $finalProtectionOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  $finalProtectionExitCode = $LASTEXITCODE
  if ($finalProtectionExitCode -ne 0) {
    if ($null -ne $protectionUpdateFailure) {
      throw "$protectionUpdateFailure The update sequence is non-atomic and the final protection state could not be read; it may be partially updated. Completed calls: $($completedProtectionUpdates -join ', ')."
    }
    throw "Branch protection was updated, but its final state could not be verified. $($finalProtectionOutput | Out-String)"
  }
  $finalProtection = ($finalProtectionOutput | Out-String) | ConvertFrom-Json
  $finalProtectionSummary = [ordered]@{
    checks = @($finalProtection.required_status_checks.checks | ForEach-Object {
      [ordered]@{ context = $_.context; app_id = $_.app_id }
    })
    admins = [bool]$finalProtection.enforce_admins.enabled
    pull_request_reviews = $null -ne $finalProtection.required_pull_request_reviews
  } | ConvertTo-Json -Depth 6 -Compress
  if ($null -ne $protectionUpdateFailure) {
    if ($completedProtectionUpdates.Count -gt 0) {
      throw "$protectionUpdateFailure The non-atomic sequence partially updated protection. Confirmed successful calls: $($completedProtectionUpdates -join ', '). Final state: $finalProtectionSummary"
    }
    throw "$protectionUpdateFailure No update call was confirmed successful. Final state: $finalProtectionSummary"
  }
  $expectedFinalAppIds = [Collections.Generic.Dictionary[string, int64]]::new(
    [StringComparer]::OrdinalIgnoreCase
  )
  foreach ($context in @($checksByContext.Keys)) {
    $expectedFinalAppIds[$context] = [int64]$checksByContext[$context].app_id
  }
  Assert-ClassicProtectionState $finalProtection $expectedFinalAppIds $expectedReviewApprovalCount
} elseif (($protectionOutput | Out-String) -match '(?is)Branch not protected.*HTTP 404') {
  # No protection exists, so a complete initial payload cannot overwrite prior policy.
  $checks = @($requiredCheckNames | ForEach-Object {
    @{ context = $_; app_id = $requiredAppIdsByContext[$_] }
  })
  $payload = @{
    required_status_checks = @{
      strict = $true
      # GitHub's request schema treats `contexts` and `checks` as alternative
      # shapes. Send only `checks` so each context keeps its verified app binding.
      checks = $checks
    }
    enforce_admins = $true
    required_pull_request_reviews = @{ required_approving_review_count = 0 }
    restrictions = $null
  } | ConvertTo-Json -Depth 6

  $requiredCheckPreflight = Assert-FreshRequiredCheckPreflight
  $protectionBeforeCreateOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  if ($LASTEXITCODE -eq 0) {
    throw "Branch protection appeared after the initial read; do not replace it. Rerun the full protection review."
  }
  if (($protectionBeforeCreateOutput | Out-String) -notmatch '(?is)Branch not protected.*HTTP 404') {
    throw "Could not verify that branch protection is still absent; do not create it. $($protectionBeforeCreateOutput | Out-String)"
  }
  $createOutput = $payload | gh api --hostname github.com -X PUT $protectionPath `
    -H "Accept: application/vnd.github+json" --input - 2>&1
  $createExitCode = $LASTEXITCODE
  if ($createExitCode -ne 0) {
    $createError = $createOutput | Out-String
    if ($createError -match '(?is)HTTP 403') {
      throw "GitHub forbade branch-protection creation. Verify repository plan and Administration permission; no protection was created. $createError"
    }
    if ($createError -match '(?is)HTTP 404') {
      throw "The repository or detected default branch was not found or is inaccessible; no protection was created. Re-verify repository identity and DEFAULT_BRANCH. $createError"
    }
    if ($createError -match '(?is)HTTP 422') {
      throw "GitHub rejected the branch-protection request as invalid or abuse-limited; no protection was created. Inspect the preserved response and correct the payload or retry policy. $createError"
    }
    throw "Failed to create branch protection. $createError"
  }
  $createdProtectionOutput = gh api --hostname github.com $protectionPath `
    -H "Accept: application/vnd.github+json" 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Branch protection creation returned success, but its final state could not be verified. $($createdProtectionOutput | Out-String)"
  }
  $createdProtection = ($createdProtectionOutput | Out-String) | ConvertFrom-Json
  Assert-ClassicProtectionState $createdProtection $requiredAppIdsByContext 0
} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 403') {
  throw "GitHub forbade branch-protection inspection. Verify repository plan and Administration permission; no settings were changed. $($protectionOutput | Out-String)"
} elseif (($protectionOutput | Out-String) -match '(?is)HTTP 404') {
  throw "The repository or detected default branch was not found or is inaccessible. Re-verify repository identity and DEFAULT_BRANCH; no settings were changed. $($protectionOutput | Out-String)"
} else {
  throw "Could not read existing branch protection; no settings were changed. $($protectionOutput | Out-String)"
}
```

`ci-success` is the aggregate test gate shipped in `assets/workflows/ci.yml`: it is green only if every `test` matrix job passed. Requiring it instead of every matrix combination keeps the matrix-specific list stable. Dependency review and the dependency-free Conventional Commit gate (`commitlint`) are independent jobs, so they must be required separately when their repo-scaffold assets were installed. All three shipped required-check workflows include unfiltered `pull_request` and `merge_group` coverage. Existing workflows may use different job IDs, names, triggers, or filters; preserve those files and require only contexts verified from their actual definitions and event coverage.

GitHub's protected-branch API exposes separate writes for these settings and
does not document conditional writes for them. Before each write the example
reruns required-check evidence and re-reads the affected state. It compares
required-check bindings with the initial snapshot before replacing them,
preserves review protection that appears concurrently, and confirms protection
is still absent before creating it. A changed state stops the sequence and the
final GET reports partial updates. A request-sized race remains after each last
read; coordinate a single-writer window or do not automate this path when that
residual race is unacceptable. Never claim the sequence is atomic.

## Ruleset compatibility (inspect only)

Rulesets can coexist with classic branch protection, and the most restrictive applicable rule wins. This plugin configures only classic branch protection because safely creating or editing a ruleset requires preserving repository and organization policy, bypass actors, target conditions, and rule-specific parameters. Do not call `POST`/`PUT`/`DELETE repos/OWNER/REPO/rulesets` as part of this scaffold and do not claim that a ruleset was configured.

Before changing classic protection or installing auto-merge, inspect the effective rules on the detected default branch with `repos/OWNER/REPO/rules/branches/BRANCH`. Report repository and organization rulesets as existing policy, preserve them, and stop when an effective rule conflicts with the proposed classic settings. In a ruleset `required_status_checks` rule, the source-binding field is `integration_id`, not classic protection's `app_id`. If the user wants a ruleset created or changed, treat that as a separate policy-design task requiring explicit approval and a complete reviewed payload.

## Labels

GitHub creates these default labels for new repositories, but they can be edited
or deleted. Recreate them if missing:

```powershell
$repositorySettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/repository_settings_preflight.py"
if (-not (Test-Path -LiteralPath $repositorySettingsPreflight -PathType Leaf)) {
  throw "The bundled repository-settings preflight is missing; do not create labels."
}
# Populate this list only from the exact optional assets selected in the
# approved scaffold plan. The allowlist and labels come from the checked-in assets.
$selectedOptionalLabelAssets = @() # e.g. "labeler.yml" only when that asset is selected
$optionalLabelNamesByAsset = [ordered]@{
  "auto-merge.yml" = @("automerge")
  "labeler.yml" = @("ci", "tests")
  "release-config.yml" = @("feature", "fix", "ignore-for-release")
  "stale.yml" = @("Stale", "pinned", "security")
}
$allowedOptionalLabelAssets = @(
  "auto-merge.yml", "labeler.yml", "release-config.yml", "stale.yml"
)
$selectedOptionalLabelAssetSet = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::Ordinal
)
foreach ($assetName in @($selectedOptionalLabelAssets)) {
  if ($assetName -isnot [string] -or
      $allowedOptionalLabelAssets -cnotcontains $assetName -or
      -not $selectedOptionalLabelAssetSet.Add($assetName)) {
    throw "Label plan contains an unsupported or duplicate optional asset: '$assetName'."
  }
}
$plannedLabelNames = @(
  "bug", "documentation", "duplicate", "enhancement", "good first issue",
  "help wanted", "invalid", "question", "wontfix"
)
foreach ($assetName in $selectedOptionalLabelAssets) {
  $plannedLabelNames += @($optionalLabelNamesByAsset[[string]$assetName])
}
$plannedLabelNameSet = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($labelName in $plannedLabelNames) {
  if (-not $plannedLabelNameSet.Add([string]$labelName)) {
    throw "Label plan contains a duplicate label: '$labelName'."
  }
}
$labelPreflightArguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
foreach ($labelName in $plannedLabelNames) { $labelPreflightArguments += @("--create-label", $labelName) }
$labelPreflightOutput = python $repositorySettingsPreflight @labelPreflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Label inspection is inconclusive; do not create labels. $($labelPreflightOutput | Out-String)"
}
try {
  $labelPreflightText = $labelPreflightOutput | Out-String
  if (-not $labelPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
  $labelPreflight = $labelPreflightText | ConvertFrom-Json -ErrorAction Stop
} catch {
  throw "Repository-settings preflight returned invalid JSON; do not create labels."
}
Assert-SelectedRepositoryId -RepositoryId $labelPreflight.repository_id
if ($labelPreflight -isnot [pscustomobject] -or
    $labelPreflight.repository -isnot [string] -or $labelPreflight.decision -isnot [string] -or
    $labelPreflight.inspection_complete -isnot [bool] -or
    -not $labelPreflight.inspection_complete -or
    $labelPreflight.decision -ne "may-configure-repository-settings" -or
    @($labelPreflight.requested_mutations) -notcontains "labels") {
  throw "Repository-settings preflight did not approve the planned label mutations."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$labelPreflight.repository, "OWNER/REPO"
)) {
  throw "Repository-settings preflight returned a different repository; do not create labels."
}
$approvedLabelSettings = $labelPreflight.requested_settings
if ($approvedLabelSettings -isnot [pscustomobject] -or
    $approvedLabelSettings.labels -isnot [Array] -or
    @($approvedLabelSettings.labels | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
    $labelPreflight.requested_mutations -isnot [Array] -or
    $labelPreflight.requested_mutations.Count -ne 1 -or
    $labelPreflight.requested_mutations[0] -isnot [string] -or
    $labelPreflight.requested_mutations[0] -cne "labels") {
  throw "Repository-settings preflight did not return the approved label input."
}
$approvedLabels = @($approvedLabelSettings.labels)
if ($approvedLabels.Count -ne $plannedLabelNames.Count -or
    $null -ne (Compare-Object -ReferenceObject $plannedLabelNames -DifferenceObject $approvedLabels -CaseSensitive)) {
  throw "Repository-settings preflight input does not match the planned label mutations."
}

$existingLabels = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
$labelListOutput = gh api --hostname github.com --paginate `
  "repos/OWNER/REPO/labels?per_page=100" --jq '.[].name'
$labelListExitCode = $LASTEXITCODE
if ($labelListExitCode -ne 0) { throw "Failed to list existing labels; no labels were changed." }
foreach ($labelName in @($labelListOutput)) {
  [void]$existingLabels.Add([string]$labelName)
}

function Assert-CurrentLabelRepository {
  $output = gh api --hostname github.com repos/OWNER/REPO 2>&1
  if ($LASTEXITCODE -ne 0) { throw "Label target identity could not be verified; state remains unverified." }
  $text = $output | Out-String
  if (-not $text.TrimStart().StartsWith("{")) { throw "Label target response must be an object." }
  $target = $text | ConvertFrom-Json -ErrorAction Stop
  Assert-SelectedRepositoryId -RepositoryId $target.id
  if ($target -isnot [pscustomobject] -or $target.full_name -isnot [string] -or
      $target.archived -isnot [bool] -or $target.disabled -isnot [bool] -or
      $target.archived -or $target.disabled -or
      -not [StringComparer]::OrdinalIgnoreCase.Equals($target.full_name, "OWNER/REPO")) {
    throw "Label readback belongs to an unverified repository; stop configuration."
  }
}

function Get-BoundActiveLabel {
  param([Parameter(Mandatory)][string]$Name)

  Assert-CurrentLabelRepository
  $label = $null
  try {
    $encodedName = [Uri]::EscapeDataString($Name)
    $output = gh api --hostname github.com "repos/OWNER/REPO/labels/$encodedName" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Label '$Name' could not be observed; state remains unverified." }
    $text = $output | Out-String
    if (-not $text.TrimStart().StartsWith("{")) { throw "Label readback must be an object." }
    $label = $text | ConvertFrom-Json -ErrorAction Stop
    if ($label -isnot [pscustomobject] -or
        ($label.id -isnot [int] -and $label.id -isnot [long]) -or $label.id -le 0 -or
        $label.name -isnot [string] -or $label.color -isnot [string] -or
        $label.color -notmatch '^[0-9a-fA-F]{6}$' -or
        $null -eq $label.PSObject.Properties['description'] -or
        ($null -ne $label.description -and $label.description -isnot [string]) -or
        $null -eq $label.PSObject.Properties['archived_at'] -or
        $null -eq $label.PSObject.Properties['archived_by'] -or
        $null -ne $label.archived_at -or $null -ne $label.archived_by -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($label.name, $Name)) {
      throw "Label '$Name' has a malformed, mismatched or archived receipt; do not use it as an active prerequisite."
    }
  } finally {
    Assert-CurrentLabelRepository
  }
  return $label
}

function Add-LabelIfMissing {
  param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Color,
    [Parameter(Mandatory)][string]$Description
  )

  if ($existingLabels.Contains($Name)) {
    $null = Get-BoundActiveLabel -Name $Name
    return
  }
  $null = Get-ValidatedLabelPreflight -Name $Name
  $createOutput = gh label create $Name --repo github.com/OWNER/REPO `
    --color $Color --description $Description 2>&1
  $createExitCode = $LASTEXITCODE

  # Re-read the label even after a failed create. Another actor may have created it
  # after the initial list, and a successful response still needs state verification.
  $label = Get-BoundActiveLabel -Name $Name
  $labelMatches = [StringComparer]::OrdinalIgnoreCase.Equals(
    $label.name,
    $Name
  ) -and [StringComparer]::OrdinalIgnoreCase.Equals(
    $label.color,
    $Color.TrimStart("#")
  ) -and $label.description -ceq $Description
  if (-not $labelMatches) {
    throw "Label '$Name' exists after the create attempt but does not match the requested color and description. It was not overwritten."
  }
  if ($createExitCode -ne 0) {
    Write-Warning "The label create call failed, but a concurrent matching label now exists. $($createOutput | Out-String)"
  }
  [void]$existingLabels.Add($Name)
}

function Get-ValidatedLabelPreflight {
  param([Parameter(Mandatory)][string]$Name)

  $output = python $repositorySettingsPreflight `
    --repository "OWNER/REPO" --hostname "github.com" `
    --expected-repository-id $SELECTED_REPOSITORY_ID --create-label $Name 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Fresh label preflight is inconclusive for '$Name'; do not create it. $($output | Out-String)"
  }
  try {
    $preflightText = $output | Out-String
    if (-not $preflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $result = $preflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Fresh label preflight returned invalid JSON for '$Name'; do not create it."
  }
  if ($result -isnot [pscustomobject] -or
      $result.repository -isnot [string] -or $result.decision -isnot [string] -or
      $result.requested_mutations -isnot [Array] -or
      @($result.requested_mutations | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $result.requested_settings -isnot [pscustomobject] -or
      $result.requested_settings.labels -isnot [Array] -or
      @($result.requested_settings.labels | Where-Object { $_ -isnot [string] }).Count -gt 0) {
    throw "Fresh label preflight has an invalid schema; do not create the label."
  }
  $mutations = @($result.requested_mutations)
  $labels = @($result.requested_settings.labels)
  Assert-SelectedRepositoryId -RepositoryId $result.repository_id
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      $result.decision -ne "may-configure-repository-settings" -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      ) -or $mutations.Count -ne 1 -or $mutations[0] -cne "labels" -or
      $labels.Count -ne 1 -or $labels[0] -cne $Name) {
    throw "Fresh label preflight did not approve exactly '$Name' for OWNER/REPO; do not create it."
  }
  return $result
}

Add-LabelIfMissing "bug"              d73a4a "Something is not working"
Add-LabelIfMissing "documentation"    0075ca "Improvements or additions to documentation"
Add-LabelIfMissing "duplicate"        cfd3d7 "Similar issue, pull request, or discussion already exists"
Add-LabelIfMissing "enhancement"      a2eeef "New feature or request"
Add-LabelIfMissing "good first issue" 7057ff "Good for newcomers"
Add-LabelIfMissing "help wanted"      008672 "Extra attention is needed"
Add-LabelIfMissing "invalid"          e4e669 "This is no longer relevant"
Add-LabelIfMissing "question"         d876e3 "Further information is requested"
Add-LabelIfMissing "wontfix"          ffffff "This will not be worked on"
```

Create optional workflow/config labels only when the matching asset is selected
in `$selectedOptionalLabelAssets`. The shipped labeler grants
`pull-requests: write` but not `issues: write`, so every label in
`.github/labeler.yml` must already exist when that workflow runs. Run the exact
workflow-installation preflight for each selected asset, then call
`Assert-CurrentPlannedLabels` immediately before copying it. Repeat both checks
before each separate asset copy; do not reuse a label read from an earlier
preflight or copy.
Archived labels cannot be assigned by dependent workflows. Preserve their
existing state, stop the affected plan and ask for explicit remediation rather
than creating, overwriting or unarchiving them automatically. Every active-label
receipt is typed and bracketed by the original numeric repository identity;
this is current-state evidence, not an atomic named-REST guarantee.

```powershell
if ($selectedOptionalLabelAssets -contains "auto-merge.yml") {
  Add-LabelIfMissing "automerge" 0e8a16 "Auto-merge when CI is green"
}

if ($selectedOptionalLabelAssets -contains "labeler.yml") {
  Add-LabelIfMissing "ci"    5319e7 "Changes to CI configuration"
  Add-LabelIfMissing "tests" bfdadc "Changes to tests"
}

if ($selectedOptionalLabelAssets -contains "release-config.yml") {
  Add-LabelIfMissing "feature"            a2eeef "Feature work for release notes"
  Add-LabelIfMissing "fix"                d73a4a "Bug fix for release notes"
  Add-LabelIfMissing "ignore-for-release" ffffff "Exclude from generated release notes"
}

if ($selectedOptionalLabelAssets -contains "stale.yml") {
  Add-LabelIfMissing "Stale"    ededed "Inactive issue or pull request"
  Add-LabelIfMissing "pinned"   1d76db "Exempt from stale automation"
  Add-LabelIfMissing "security" b60205 "Security-related work or report"
}

$finalLabelListOutput = gh api --hostname github.com --paginate `
  "repos/OWNER/REPO/labels?per_page=100" --jq '.[].name'
if ($LASTEXITCODE -ne 0) {
  throw "Could not verify the final label state after configuration."
}
$finalLabelNames = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($labelName in @($finalLabelListOutput)) {
  [void]$finalLabelNames.Add([string]$labelName)
}
$missingFinalLabels = @($plannedLabelNames | Where-Object {
  -not $finalLabelNames.Contains([string]$_)
})
if ($missingFinalLabels.Count -gt 0) {
  throw "Some planned labels are missing from the final repository state: $($missingFinalLabels -join ', ')."
}

function Assert-CurrentPlannedLabels {
  foreach ($labelName in $plannedLabelNames) {
    $null = Get-BoundActiveLabel -Name $labelName
  }
}
Assert-CurrentPlannedLabels
```

## Dependabot

Preserve the asset's fixed root `pip` block because every scaffold installs `requirements-docs.txt`, even when the project has no application manifest. Preserve the fixed `github-actions` version- and security-update groups with `patterns: ["*"]` so all action dependencies, including multiple sub-actions from one repository, update in one pull request per update class. Render additional blocks only from manifests that actually exist in the filtered first-party candidate list. Exclude any path with a `node_modules`, `.venv`, `venv`, `vendor`, `dist`, `build`, or `target` component even when tracked, unless the user explicitly confirms that path is a first-party workspace. For every supported application ecosystem, use GitHub's exact `package-ecosystem` identifier and the repository-relative directory containing its manifest. Do not emit a duplicate root `pip` block for a root Python manifest; the fixed block already covers every supported root requirements file. Use `directories` for multiple non-overlapping additional locations of the same ecosystem, or separate blocks when their settings differ. Give every generated block `commit-message.prefix: "chore(deps)"`; Dependabot applies that prefix to both commit messages and PR titles, allowing them to pass `commitlint.yml` when that optional workflow is installed. Retain the asset's fixed `github-actions` block with `directory: "/"` so pinned workflow actions remain updateable.

Example replacement for the entire `  # {{REPO_SCAFFOLD_DEPENDABOT_PACKAGE_UPDATES}}` marker line when npm manifests exist at the root and under `packages/web`:

```yaml
  - package-ecosystem: "npm"
    directories:
      - "/"
      - "/packages/web"
    schedule:
      interval: "weekly"
    commit-message:
      prefix: "chore(deps)"
```

When there is no additional supported application location, delete the marker line and keep the fixed root `pip` and `github-actions` blocks. Otherwise, the replacement must end with a newline so the following `github-actions` list item remains valid YAML. Validate both the asset and the rendered file, and require only that the exact `{{REPO_SCAFFOLD_DEPENDABOT_PACKAGE_UPDATES}}` token is gone. Do not use a generic double-brace regex: project-owned Helm, Jinja, or Angular expressions are not repo-scaffold placeholders, and `${{ ... }}` is valid GitHub Actions syntax. Once committed as `.github/dependabot.yml`, GitHub picks it up automatically; no API call is needed.

## Security features

Inspect repository capabilities before changing settings:

```bash
gh repo view github.com/OWNER/REPO --json visibility,isFork,isArchived,hasIssuesEnabled,hasDiscussionsEnabled,owner,defaultBranchRef
gh api --hostname github.com users/OWNER --jq '.type'
gh api --hostname github.com repos/OWNER/REPO --jq '.security_and_analysis'
```

The repository view returns the owner login but not its account type. Treat the separate Users API result as `OWNER_TYPE`, accept only `User` or `Organization`, and ask instead of guessing when the lookup fails or returns another value. Use this value for CODEOWNERS selection and private dependency-review eligibility.

Run each eligible command separately after confirmation and only report a feature as enabled after its final state is verified. Treat `403` as forbidden and `404` as missing or inaccessible unless endpoint-specific evidence proves a capability limitation. Preserve and inspect every `422` response: depending on the endpoint it can indicate invalid input, ineligibility, or abuse controls, so do not automatically relabel it as a plan or permission failure. Treat `409` as an in-progress/conflicting operation and `503` as transient service unavailability. Report the verified capability, validation, or retry result and continue instead of claiming success or failing the entire scaffold.

Before the first security-feature mutation, run the bundled
`scripts/security_features_preflight.py` with exactly the approved feature
flags. It is read-only and fail-closed: it binds the repository response to the
explicit `OWNER/REPO`, rejects archived, disabled, or ambiguous repositories, validates the
published security-analysis fields, and requires current repository
administration permission. It requires secret scanning before push protection,
and permits private vulnerability reporting only for a public repository,
including a fork. It checks Dependabot alerts before automated security fixes unless
alerts were requested for prior enablement. It does not infer Secret Protection
entitlement from missing repository fields. For private or internal targets,
verify the organization's [GitHub Secret Protection eligibility](https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning)
separately. Repository-level [push protection requires Secret Protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection).
On GitHub.com, a user-owned private repository requires the documented
Enterprise Managed Users eligibility. Set
`--confirm-private-secret-protection-eligibility` only after that check, or the
preflight returns a confirmation-required decision and forbids mutation. This
flag records a user-verified plan assumption; it does not query billing state.
Continue to handle GitHub's final `403`, `404`, `409`, `422`, and `503` result
separately.

```powershell
$securityFeaturesPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/security_features_preflight.py"
if (-not (Test-Path -LiteralPath $securityFeaturesPreflight -PathType Leaf)) {
  throw "The bundled security-features preflight is missing; do not mutate security settings."
}

# Set each value only from explicit user approval.
$enableDependabotAlertsRequested = $false
$enableAutomatedSecurityFixesRequested = $false
$enableSecretScanningRequested = $false
$enablePushProtectionRequested = $false
$enablePrivateVulnerabilityReportingRequested = $false
# Set only after verifying Secret Protection eligibility for a private/internal target.
$confirmPrivateSecretProtectionEligibility = $false
foreach ($choice in @(
    "enableDependabotAlertsRequested", "enableAutomatedSecurityFixesRequested",
    "enableSecretScanningRequested", "enablePushProtectionRequested",
    "enablePrivateVulnerabilityReportingRequested", "confirmPrivateSecretProtectionEligibility")) {
  if ((Get-Variable -Name $choice -ValueOnly) -isnot [bool]) {
    throw "Security choices must be explicit Boolean values; do not infer approval."
  }
}
$securityPreflightArguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID)
if ($enableDependabotAlertsRequested) { $securityPreflightArguments += "--enable-dependabot-alerts" }
if ($enableAutomatedSecurityFixesRequested) { $securityPreflightArguments += "--enable-automated-security-fixes" }
if ($enableSecretScanningRequested) { $securityPreflightArguments += "--enable-secret-scanning" }
if ($enablePushProtectionRequested) { $securityPreflightArguments += "--enable-push-protection" }
if ($enablePrivateVulnerabilityReportingRequested) { $securityPreflightArguments += "--enable-private-vulnerability-reporting" }
if ($confirmPrivateSecretProtectionEligibility -and
    ($enableSecretScanningRequested -or $enablePushProtectionRequested)) {
  $securityPreflightArguments += "--confirm-private-secret-protection-eligibility"
}

$requestedSecurityFeatures = @()
if ($enableDependabotAlertsRequested) { $requestedSecurityFeatures += "dependabot_alerts" }
if ($enableAutomatedSecurityFixesRequested) { $requestedSecurityFeatures += "automated_security_fixes" }
if ($enableSecretScanningRequested) { $requestedSecurityFeatures += "secret_scanning" }
if ($enablePushProtectionRequested) { $requestedSecurityFeatures += "push_protection" }
if ($enablePrivateVulnerabilityReportingRequested) { $requestedSecurityFeatures += "private_vulnerability_reporting" }
if ($confirmPrivateSecretProtectionEligibility -and
    -not ($enableSecretScanningRequested -or $enablePushProtectionRequested)) {
  throw "Secret Protection eligibility confirmation requires a secret-scanning or push-protection request."
}
function Assert-SecurityFeaturePreflightSchema {
  param([object]$Verdict)
  if ($Verdict -isnot [pscustomobject]) { throw "Security verdict must be a JSON object." }
  Assert-SelectedRepositoryId -RepositoryId $Verdict.repository_id
  foreach ($field in @("inspection_complete", "administration_permission", "is_fork")) {
    if ($Verdict.$field -isnot [bool]) { throw "Security verdict has an invalid Boolean field." }
  }
  if (-not $Verdict.administration_permission) { throw "Security administration permission is unverified." }
  if ($Verdict.repository -isnot [string] -or
      -not [StringComparer]::OrdinalIgnoreCase.Equals($Verdict.repository, "OWNER/REPO") -or
      $Verdict.decision -isnot [string] -or
      $Verdict.visibility -isnot [string] -or $Verdict.visibility -cnotin @("public", "private", "internal") -or
      $Verdict.owner_type -isnot [string] -or $Verdict.owner_type -cnotin @("User", "Organization") -or
      $Verdict.private_security_feature_eligibility -isnot [string] -or
      $Verdict.private_security_feature_eligibility -cnotin @("not-required", "confirmation-required", "user-confirmed")) {
    throw "Security verdict has an invalid identity, applicability or decision field."
  }
  $knownFeatures = @("dependabot_alerts", "automated_security_fixes", "secret_scanning", "push_protection", "private_vulnerability_reporting")
  if ($Verdict.requested_features -isnot [array] -or $Verdict.requested_features.Count -lt 1 -or
      $Verdict.requested_features.Count -gt $knownFeatures.Count) { throw "Security feature plan is missing or out of bounds." }
  $features = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach ($feature in $Verdict.requested_features) {
    if ($feature -isnot [string] -or $feature -cnotin $knownFeatures -or -not $features.Add($feature)) {
      throw "Security feature plan contains a malformed or duplicate entry."
    }
  }
  if ($Verdict.security_and_analysis -isnot [pscustomobject]) { throw "Security analysis evidence must be an object." }
  foreach ($field in @("dependabot_security_updates", "secret_scanning", "secret_scanning_push_protection")) {
    if ($null -eq $Verdict.security_and_analysis.PSObject.Properties[$field] -or
        ($null -ne $Verdict.security_and_analysis.$field -and
         ($Verdict.security_and_analysis.$field -isnot [string] -or $Verdict.security_and_analysis.$field -cnotin @("enabled", "disabled")))) {
      throw "Security status evidence is missing or malformed."
    }
  }
  if ($null -eq $Verdict.PSObject.Properties['dependabot_alerts_precondition']) { throw "Dependabot prerequisite evidence is missing." }
  if ($features.Contains("private_vulnerability_reporting") -and $Verdict.visibility -cne "public") {
    throw "Private vulnerability reporting requires a public repository."
  }
  if ($features.Contains("push_protection") -and -not $features.Contains("secret_scanning") -and
      $Verdict.security_and_analysis.secret_scanning -cne "enabled") {
    throw "Push protection has no observed or approved prior secret-scanning prerequisite."
  }
  if ($features.Contains("automated_security_fixes")) {
    if ($Verdict.dependabot_alerts_precondition -isnot [string] -or
        $Verdict.dependabot_alerts_precondition -cnotin @("verified-enabled", "requested-for-prior-enable") -or
        ($Verdict.dependabot_alerts_precondition -ceq "requested-for-prior-enable" -and -not $features.Contains("dependabot_alerts"))) {
      throw "Automated fixes have no valid observed or prior-enable prerequisite."
    }
  } elseif ($null -ne $Verdict.dependabot_alerts_precondition) {
    throw "An unrelated feature plan cannot reuse automated-fix prerequisite evidence."
  }
}

$securityPreflightResult = $null
$approvedSecurityFeatures = @()
if ($requestedSecurityFeatures.Count -gt 0) {
  $securityPreflightOutput = python $securityFeaturesPreflight @securityPreflightArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Security-feature inspection is inconclusive; do not mutate. $($securityPreflightOutput | Out-String)"
  }
  try {
    $securityVerdictText = $securityPreflightOutput | Out-String
    if ([Text.Encoding]::UTF8.GetByteCount($securityVerdictText) -gt 2097152) { throw "Security verdict exceeds the byte limit." }
    $securityPreflightResult = $securityVerdictText | ConvertFrom-Json
  } catch {
    throw "Security-feature preflight returned invalid JSON; do not mutate."
  }
  Assert-SecurityFeaturePreflightSchema -Verdict $securityPreflightResult
  if ($securityPreflightResult.decision -eq "confirm-private-secret-protection-eligibility") {
    throw "Verify GitHub Secret Protection eligibility for this private/internal repository, then set `$confirmPrivateSecretProtectionEligibility and rerun the preflight."
  }
  Assert-SelectedRepositoryId -RepositoryId $securityPreflightResult.repository_id
  if ($securityPreflightResult.inspection_complete -isnot [bool] -or
      -not $securityPreflightResult.inspection_complete -or
      $securityPreflightResult.decision -ne "may-configure-security-features") {
    throw "Security-feature preflight did not approve the requested mutations."
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$securityPreflightResult.repository,
    "OWNER/REPO"
  )) {
    throw "Security-feature preflight returned a different repository; do not mutate."
  }
  $approvedSecurityFeatures = @($securityPreflightResult.requested_features | ForEach-Object { [string]$_ })
  if ($approvedSecurityFeatures.Count -ne $requestedSecurityFeatures.Count -or
      $null -ne (Compare-Object -ReferenceObject $requestedSecurityFeatures -DifferenceObject $approvedSecurityFeatures -CaseSensitive)) {
    throw "Security-feature preflight input does not match the requested mutations."
  }
  $privateSecretFeatureRequested =
    $securityPreflightResult.visibility -ne "public" -and
    @($requestedSecurityFeatures | Where-Object { $_ -in @("secret_scanning", "push_protection") }).Count -gt 0
  $expectedSecretProtectionEligibility = if ($privateSecretFeatureRequested) {
    "user-confirmed"
  } else {
    "not-required"
  }
  if ($securityPreflightResult.private_security_feature_eligibility -cne
      $expectedSecretProtectionEligibility) {
    throw "Secret Protection eligibility evidence does not match the requested features and repository visibility."
  }
} else {
  Write-Output "No security-feature changes were requested; skip the preflight and mutation blocks."
}
```

An approved feature list is only the planned mutation envelope. Re-run the
read-only preflight for each individual setting immediately before its write,
bind its repository and single requested feature, check the write exit code,
and read the setting back. A failed or inconclusive feature check skips only
that feature and any dependent setting; continue unrelated scaffold work without
claiming that feature was enabled.
Both initial and per-writer results must pass the shared schema before any
casts or applicability decisions. It requires typed permission/target/owner/
visibility/fork fields, a bounded unique known-feature array and explicit
nullable security/prerequisite evidence. Wrong JSON shapes cannot authorize a
mutation, and optional null evidence is not passed enablement. JSON text is
byte-bounded before parsing. Keep user approval distinct from this schema check.
For a multi-request inspection, the producer rechecks the Dependabot prerequisite
and then binds current numeric identity, active/admin/visibility/owner/fork and
security-analysis state to its original snapshot. Any observed drift invalidates
the verdict. A `verified-enabled` prerequisite requires the exact `204 No Content`
HTTP receipt, not merely a successful CLI exit or arbitrary response text. The
separate `requested-for-prior-enable` decision still means alerts must first be
enabled and freshly verified; it is not observed enablement.
Read back after every attempted write, even when its acknowledgment fails: an
error does not prove that remote state stayed unchanged. Keep acknowledgment
and observed current state separate, preserve the response, and do not roll
back or replay automatically. Malformed Boolean/status fields or an unreadable
readback are unverified, not enablement. Bind repository-status readbacks to
the selected repository before accepting their security-analysis fields.

```powershell
function Get-ValidatedSecurityFeaturePreflight {
  param(
    [Parameter(Mandatory)][string[]]$FeatureArguments,
    [Parameter(Mandatory)][string]$ExpectedFeature,
    [switch]$ConfirmPrivateSecretProtectionEligibility
  )

  $arguments = @("--repository", "OWNER/REPO", "--hostname", "github.com", "--expected-repository-id", $SELECTED_REPOSITORY_ID) + @($FeatureArguments)
  if ($ConfirmPrivateSecretProtectionEligibility -and
      $ExpectedFeature -in @("secret_scanning", "push_protection")) {
    $arguments += "--confirm-private-secret-protection-eligibility"
  }
  $output = & python $securityFeaturesPreflight @arguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    Write-Warning "Security-feature preflight is inconclusive for $ExpectedFeature; skip this mutation. $($output | Out-String)"
    return $null
  }
  try {
    $securityVerdictText = $output | Out-String
    if ([Text.Encoding]::UTF8.GetByteCount($securityVerdictText) -gt 2097152) { throw "Security verdict exceeds the byte limit." }
    $result = $securityVerdictText | ConvertFrom-Json
  } catch {
    Write-Warning "Security-feature preflight returned invalid JSON for $ExpectedFeature; skip this mutation."
    return $null
  }
  Assert-SecurityFeaturePreflightSchema -Verdict $result
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      $result.decision -ne "may-configure-security-features" -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      )) {
    Write-Warning "Security-feature preflight did not approve $ExpectedFeature for OWNER/REPO; skip this mutation."
    return $null
  }
  $features = @($result.requested_features | ForEach-Object { [string]$_ })
  if ($features.Count -ne 1 -or $features[0] -cne $ExpectedFeature) {
    Write-Warning "Security-feature preflight input does not match $ExpectedFeature; skip this mutation."
    return $null
  }
  $requiresSecretProtectionConfirmation =
    $ExpectedFeature -in @("secret_scanning", "push_protection") -and
    $result.visibility -ne "public"
  if ($requiresSecretProtectionConfirmation) {
    if (-not $ConfirmPrivateSecretProtectionEligibility -or
        $result.private_security_feature_eligibility -cne "user-confirmed") {
      Write-Warning "Private Secret Protection eligibility is not confirmed for $ExpectedFeature; skip this mutation."
      return $null
    }
  } elseif ($result.private_security_feature_eligibility -cne "not-required") {
    Write-Warning "Security-feature preflight returned unexpected Secret Protection eligibility evidence; skip this mutation."
    return $null
  }
  return $result
}
```

- **Dependency graph**: enabled by default for public repositories. It is not the same setting as Dependabot alerts.
- **Dependabot alerts**: not enabled by default. Enable explicitly where supported. Dependabot alerts before automated security fixes are required: when both were approved in one preflight, enable alerts, re-query `vulnerability-alerts` successfully, and only then enable security fixes:

  ```powershell
  if ($enableDependabotAlertsRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-dependabot-alerts") `
      -ExpectedFeature "dependabot_alerts"
    if ($null -ne $featurePreflight) {
      $enableOutput = gh api --hostname github.com -X PUT `
        repos/OWNER/REPO/vulnerability-alerts 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Dependabot alerts write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com --include `
        repos/OWNER/REPO/vulnerability-alerts 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Dependabot alerts are not verified as enabled after the attempted write. $($verifyOutput | Out-String)"
      } else {
        $alertReceipt = $verifyOutput | Out-String
        if ([Text.Encoding]::UTF8.GetByteCount($alertReceipt) -gt 2097152 -or
            $alertReceipt -notmatch '\AHTTP/[0-9]+(?:\.[0-9]+)? 204[^\r\n]*\r?\n(?:[!#$%&''*+.^_`|~0-9A-Za-z-]+:[^\r\n]*\r?\n)*\r?\n[ \t\r\n]*\z') {
          Write-Warning "Dependabot alerts readback is not an exact no-content 204 receipt; do not claim enablement."
        }
      }
    }
  }
  if ($enableAutomatedSecurityFixesRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-automated-security-fixes") `
      -ExpectedFeature "automated_security_fixes"
    if ($null -ne $featurePreflight) {
      $enableOutput = gh api --hostname github.com -X PUT `
        repos/OWNER/REPO/automated-security-fixes 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Automated security fixes write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com `
        repos/OWNER/REPO/automated-security-fixes 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Automated security fixes state could not be verified after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $fixState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch {
          $fixState = $null
        }
        if ($null -eq $fixState -or
            $fixState.enabled -isnot [bool] -or $fixState.enabled -ne $true -or
            $fixState.paused -isnot [bool] -or $fixState.paused -ne $false) {
          Write-Warning "Automated security fixes are not verified as enabled and unpaused."
        }
      }
    }
  }
  ```

- **Secret scanning + push protection**: availability depends on repository visibility and the owner's GitHub security entitlement. Push protection requires secret scanning. Attempt only after the capability check:

  ```powershell
  if ($enableSecretScanningRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-secret-scanning") `
      -ExpectedFeature "secret_scanning" `
      -ConfirmPrivateSecretProtectionEligibility:$confirmPrivateSecretProtectionEligibility
    if ($null -ne $featurePreflight) {
      $enableOutput = gh repo edit github.com/OWNER/REPO --enable-secret-scanning 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Secret scanning write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Secret-scanning state could not be read after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $secretState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch { $secretState = $null }
        if ($null -ne $secretState) { Assert-SelectedRepositoryId -RepositoryId $secretState.id }
        if ($null -eq $secretState -or $secretState.full_name -isnot [string] -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals($secretState.full_name, "OWNER/REPO") -or
            $secretState.security_and_analysis.secret_scanning.status -isnot [string] -or
            $secretState.security_and_analysis.secret_scanning.status -cne "enabled") {
          Write-Warning "Secret scanning is not verified as enabled for OWNER/REPO after the attempted write."
        }
      }
    }
  }
  if ($enablePushProtectionRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-push-protection") `
      -ExpectedFeature "push_protection" `
      -ConfirmPrivateSecretProtectionEligibility:$confirmPrivateSecretProtectionEligibility
    if ($null -ne $featurePreflight) {
      $enableOutput = gh repo edit github.com/OWNER/REPO `
        --enable-secret-scanning-push-protection 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Push protection write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Push-protection state could not be read after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $pushProtectionState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch { $pushProtectionState = $null }
        if ($null -ne $pushProtectionState) { Assert-SelectedRepositoryId -RepositoryId $pushProtectionState.id }
        if ($null -eq $pushProtectionState -or $pushProtectionState.full_name -isnot [string] -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals($pushProtectionState.full_name, "OWNER/REPO") -or
            $pushProtectionState.security_and_analysis.secret_scanning.status -isnot [string] -or
            $pushProtectionState.security_and_analysis.secret_scanning.status -cne "enabled" -or
            $pushProtectionState.security_and_analysis.secret_scanning_push_protection.status -isnot [string] -or
            $pushProtectionState.security_and_analysis.secret_scanning_push_protection.status -cne "enabled") {
          Write-Warning "Push protection and its secret-scanning prerequisite were not both verified as enabled for OWNER/REPO."
        }
      }
    }
  }
  ```

- **CodeQL advanced setup**: when the user explicitly chooses a repository-managed configuration, run the bundled `advanced_codeql_preflight.py` before installing `assets/workflows/codeql.yml`. It requires GitHub Actions, requires GitHub Code Security for private/internal repositories, and delegates the bounded workflow, analysis, and external-uploader inspection to the existing CodeQL preflight. Render the verified default branch through `{{REPO_SCAFFOLD_DEFAULT_BRANCH_GLOB_JSON_ESCAPED}}` plus a supported detected language, and keep CodeQL default setup not configured. Do not install a second advanced uploader silently. If default setup is already configured, stop and obtain explicit approval before switching modes.

  CodeQL advanced setup, dependency review, and Scorecard inspect
  `security_and_analysis.code_security.status` for private/internal repositories.
  They use legacy `advanced_security.status` only when `code_security` is absent;
  an explicit disabled or malformed value cannot fall back to legacy evidence.

  ```powershell
  if (-not (Get-Variable REPO_ROOT -ErrorAction SilentlyContinue)) {
    throw "REPO_ROOT must be the surveyed target repository root before inspecting CodeQL setup."
  }
  if ([string]::IsNullOrWhiteSpace($DEFAULT_BRANCH)) {
    throw "DEFAULT_BRANCH must be known before inspecting CodeQL setup."
  }
  $advancedCodeqlPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/advanced_codeql_preflight.py"
  if (-not (Test-Path -LiteralPath $advancedCodeqlPreflight -PathType Leaf)) {
    throw "The bundled advanced CodeQL preflight is missing; do not copy codeql.yml."
  }
  # Set this to $true only after the user explicitly confirms that no external
  # or indirect process uploads CodeQL results.
  $advancedCodeqlNoExternalConfirmed = $false
  if ($advancedCodeqlNoExternalConfirmed -isnot [bool] -or -not $advancedCodeqlNoExternalConfirmed) {
    throw "Explicit confirmation of no external or indirect CodeQL uploader is required; do not copy codeql.yml."
  }
  $advancedCodeqlOutput = python $advancedCodeqlPreflight `
    --repo-root $REPO_ROOT `
    --repository "OWNER/REPO" `
    --expected-repository-id $SELECTED_REPOSITORY_ID `
    --default-branch $DEFAULT_BRANCH `
    --hostname "github.com" `
    --confirm-no-external-codeql 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Advanced CodeQL inspection is inconclusive; do not copy codeql.yml. $($advancedCodeqlOutput | Out-String)"
  }
  $advancedCodeqlText = $advancedCodeqlOutput | Out-String
  if (-not $advancedCodeqlText.TrimStart().StartsWith("{")) { throw "Advanced CodeQL preflight must be an object." }
  $advancedCodeqlResult = $advancedCodeqlText | ConvertFrom-Json -ErrorAction Stop
  Assert-SelectedRepositoryId -RepositoryId $advancedCodeqlResult.repository_id
  if ($advancedCodeqlResult -isnot [pscustomobject] -or
      $advancedCodeqlResult.repository -isnot [string] -or
      $advancedCodeqlResult.decision -isnot [string] -or
      $advancedCodeqlResult.default_branch -isnot [string] -or
      $advancedCodeqlResult.inspection_complete -isnot [bool] -or
      -not $advancedCodeqlResult.inspection_complete -or
      $advancedCodeqlResult.decision -ne "may-install-advanced-codeql-workflow") {
    throw "Advanced CodeQL setup is not eligible. Resolve the returned decision and rerun before copying codeql.yml."
  }
  if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      [string]$advancedCodeqlResult.repository, "OWNER/REPO"
    ) -or [string]$advancedCodeqlResult.default_branch -cne $DEFAULT_BRANCH) {
    throw "Advanced CodeQL preflight result is not bound to OWNER/REPO and DEFAULT_BRANCH; do not copy codeql.yml."
  }
  ```

  Then run `workflow_installation_preflight.py` against `codeql.yml`; this
  separate gate verifies its exact action pins against the effective Actions
  policy before the asset is copied.
  The nested default/advanced inspection must return the same numeric target ID
  and exact default branch; a child verdict for another target cannot authorize
  this asset. Nested reads share one API request/byte/deadline budget, and final
  repository/Actions/default-setup ownership state must remain unchanged.
  Unbound or drifted evidence forbids installation. Configured-default
  preservation may leave workflow, analysis and administration evidence unknown;
  those `null` values are not proof that those inspections passed.

- **Scorecard SARIF upload**: `scorecard.yml` uploads third-party SARIF results
  to code scanning. GitHub documents this in [Uploading a SARIF file to
  GitHub](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/integrate-with-existing-tools/upload-sarif-file): public repositories are eligible, while private/internal repositories require an organization-owned target with GitHub Code Security enabled. Before copying the asset, run the bundled capability preflight and proceed only when it returns `may-install-scorecard-workflow`; then run the workflow-installation preflight for its exact action pins.

  ```powershell
  $scorecardPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/scorecard_preflight.py"
  if (-not (Test-Path -LiteralPath $scorecardPreflight -PathType Leaf)) {
    throw "The bundled Scorecard preflight is missing; do not copy scorecard.yml."
  }
  $scorecardPreflightOutput = python $scorecardPreflight --repository "OWNER/REPO" `
    --expected-repository-id $SELECTED_REPOSITORY_ID 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Scorecard preflight was inconclusive; do not copy scorecard.yml. $($scorecardPreflightOutput | Out-String)"
  }
  $scorecardPreflightText = $scorecardPreflightOutput | Out-String
  if (-not $scorecardPreflightText.TrimStart().StartsWith("{")) {
    throw "Scorecard preflight must return a JSON object, not an array or scalar."
  }
  $scorecardPreflightResult = $scorecardPreflightText | ConvertFrom-Json
  if ($scorecardPreflightResult -isnot [System.Management.Automation.PSCustomObject]) {
    throw "Scorecard preflight returned a non-object verdict; do not copy scorecard.yml."
  }
  Assert-SelectedRepositoryId -RepositoryId $scorecardPreflightResult.repository_id
  if ($scorecardPreflightResult.inspection_complete -isnot [bool] -or
      -not $scorecardPreflightResult.inspection_complete -or
      $scorecardPreflightResult.decision -isnot [string] -or
      $scorecardPreflightResult.decision -ne "may-install-scorecard-workflow") {
    throw "Scorecard is not eligible. Resolve the returned decision and rerun before copying scorecard.yml."
  }
  if ($scorecardPreflightResult.repository -isnot [string] -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$scorecardPreflightResult.repository, "OWNER/REPO"
  )) {
    throw "Scorecard preflight returned a different repository; do not copy scorecard.yml."
  }
  ```

- **Code scanning default setup**: requires GitHub Actions and code-scanning eligibility. GitHub can enable default setup without a currently supported language, but no scan runs until a supported language is added; report that distinction and do not claim scan coverage from configuration state alone. Skip this mutation path when the repository-managed advanced workflow was selected. Otherwise, first inspect the current default-setup state, direct workflow evidence in the working tree and default branch, and existing CodeQL analyses. Separately ask whether external CI, indirect scripts, local actions, composite actions, or any other process uploads CodeQL results. Do not infer their absence from repository workflow inspection. Do not treat a generic request to enable code scanning as permission to replace advanced setup: switching disables its workflow and blocks CodeQL analysis API uploads.

  The bundled preflight requires PyYAML and a Python feature release at or above
  `tooling-python-minimum` in `.github/ci-toolchain.json`. When default setup is
  not configured, the mutation-mode check requires repository administration,
  an active repository, enabled Actions, and the exact current default branch
  before inspecting remote workflows. Remote tree entries using symlink or
  non-file modes fail closed instead of being parsed as workflow YAML.
  Reusable workflow lookup selects its regular file from the immutable Git
  tree, not the Contents API's potentially dereferenced symlink response.
  Bound tree work before indexing paths, reject partial or ambiguous entries,
  and verify raw Git blob bytes before parsing or caching signals. Cache only
  within the same inspection's exact repository/commit/path identity; a failed
  read must not become reusable absence evidence.
  The default-branch root inventory applies the same typed-path and total-entry
  admission: an unknown entry must not disappear into a claim of no advanced
  workflow. Incomplete inventory remains inconclusive before mutation.
  A full literal reusable-workflow commit pin must equal the resolved commit
  before tree/blob reads or ref/signal cache use. Do not replace a pin with a
  different well-formed SHA. Matching IDs retain case-insensitive comparison;
  supported symbolic branch/tag refs still resolve to their returned snapshot.
  This binding does not prove commit membership or an executed workflow.

  Resolve `REPO_SCAFFOLD_SKILL_ROOT` to the installed/source directory that contains this skill's `SKILL.md`; do not guess it from the current working directory. Run the bundled structural preflight with an available Python interpreter. It uses PyYAML's non-coercing `BaseLoader`, rejects duplicate keys, inspects only direct files under `.github/workflows`, inspects semantic `jobs.*.uses`, `jobs.*.steps[*].uses`, and shell-aware executable `run` content, and honors step, job, and workflow shell selection. For recognized Bash and PowerShell shells it masks inert heredoc, here-string, arithmetic-shift, literal, comment, and uninvoked function content, including function definitions whose opening brace is on the following line. It retains transitively invoked function bodies, literal `eval` and trap handlers, exported functions invoked by literal nested-shell commands, statically resolvable Bash/PowerShell aliases, direct shell-heredoc, recognized command wrappers, GNU `env` split strings, `xargs` with supported GNU/BSD options, direct `find` executors, shell `-c`, pipeline-fed shells, backtick/`$()` command substitution, Bash process substitution, PowerShell scriptblocks, nested PowerShell `-Command`, `Invoke-Expression`, `Start-Process`, direct `cmd /c` or `/k` CodeQL commands, quoted call-operator commands, and PowerShell `$()` execution. An unresolved command position, call-operator expression, recognized dynamic executor or alias target, encoded PowerShell command with a non-literal payload, or a malformed or unterminated construct fails closed. An unsupported or unresolved effective shell also fails closed instead of falling back to raw-text inspection. If default setup is already configured, it returns the safe preserve decision without the unnecessary workflow/analysis queries, sets those uninspected evidence fields to `null`, and sets `workflow_inspection_performed` and `analysis_inspection_performed` to false. Any other state must be exactly `not-configured`; an unknown default-setup state fails closed. It follows reusable workflows per top-level caller, rejects cycles, enforces GitHub's limit of 50 unique called workflows and 10 total levels on every call path, retains a separate 500-edge traversal safety cap, bounds API requests, and applies a timeout to each `gh api` subprocess. If Python, PyYAML, the effective shell, shell syntax, a workflow, a linked path, an API response, or the separate external/indirect CodeQL confirmation is unavailable, it exits inconclusive and mutation remains forbidden.

  Initialize `$noExternalCodeqlConfirmed` to `$false`. Set it to `$true` only after the user explicitly confirms that no external CI, indirect script, local action, composite action, or other process uploads CodeQL results. This confirmation is distinct from general scaffold approval and from approval to switch when advanced-setup evidence exists.

  ```powershell
  if (-not (Get-Variable REPO_ROOT -ErrorAction SilentlyContinue)) {
    throw "REPO_ROOT must be the surveyed target repository root before inspecting CodeQL setup."
  }
  if (-not (Get-Variable REPO_SCAFFOLD_SKILL_ROOT -ErrorAction SilentlyContinue)) {
    throw "REPO_SCAFFOLD_SKILL_ROOT must identify the directory containing this skill's SKILL.md."
  }
  if ([string]::IsNullOrWhiteSpace($DEFAULT_BRANCH)) {
    throw "DEFAULT_BRANCH must be known before inspecting CodeQL setup."
  }

  $preflightScript = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/codeql_preflight.py"
  if (-not (Test-Path -LiteralPath $preflightScript -PathType Leaf)) {
    throw "The bundled CodeQL preflight script is missing; do not PATCH default setup."
  }
  $toolchainPolicyPath = Join-Path $REPO_ROOT ".github/ci-toolchain.json"
  if (-not (Test-Path -LiteralPath $toolchainPolicyPath -PathType Leaf)) {
    throw "The CI toolchain policy is missing; do not PATCH default setup."
  }
  try {
    $toolchainPolicy = Get-Content -LiteralPath $toolchainPolicyPath -Raw -Encoding UTF8 |
      ConvertFrom-Json
    $minimumPython = $toolchainPolicy.'tooling-python-minimum'
  } catch {
    throw "The CI toolchain policy is invalid; do not PATCH default setup."
  }
  if ($minimumPython -notmatch '^3\.(0|[1-9][0-9]*)$') {
    throw "The tooling-python-minimum policy value is invalid; do not PATCH default setup."
  }
  $pythonCommand = $null
  foreach ($pythonName in @("python3", "python")) {
    $candidate = Get-Command $pythonName -ErrorAction SilentlyContinue
    if ($null -eq $candidate) { continue }
    & $candidate.Source -c "import sys, yaml; required=tuple(map(int, sys.argv[1].split('.'))); raise SystemExit(0 if sys.version_info[:2] >= required else 1)" $minimumPython 2>$null
    if ($LASTEXITCODE -eq 0) {
      $pythonCommand = $candidate
      break
    }
  }
  $defaultSetupEnablementApproved = $false
  $defaultSetupSwitchApproved = $false
  $noExternalCodeqlConfirmed = $false

  if ($null -eq $pythonCommand) {
    Write-Warning "No Python $minimumPython or newer interpreter with PyYAML is available for structural workflow inspection; do not PATCH default setup."
  } else {
    $preflightArguments = @(
      "--repo-root", $REPO_ROOT,
      "--repository", "OWNER/REPO",
      "--expected-repository-id", $SELECTED_REPOSITORY_ID,
      "--default-branch", $DEFAULT_BRANCH,
      "--hostname", "github.com",
      "--require-administration-permission"
    )
    if ($noExternalCodeqlConfirmed) {
      $preflightArguments += "--confirm-no-external-codeql"
    }
    $preflightOutput = & $pythonCommand.Source $preflightScript @preflightArguments 2>&1
    $preflightExitCode = $LASTEXITCODE
    if ($preflightExitCode -ne 0) {
      Write-Warning "CodeQL setup inspection is inconclusive; do not PATCH. $($preflightOutput | Out-String)"
    } else {
      try {
        $preflightText = $preflightOutput | Out-String
        if (-not $preflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
        $preflight = $preflightText | ConvertFrom-Json -ErrorAction Stop
      } catch {
        throw "CodeQL preflight returned invalid JSON; do not PATCH."
      }
      Assert-SelectedRepositoryId -RepositoryId $preflight.repository_id
      if ($preflight -isnot [pscustomobject] -or
          $preflight.repository -isnot [string] -or $preflight.default_branch -isnot [string] -or
          $preflight.decision -isnot [string] -or $preflight.default_setup_state -isnot [string]) {
        throw "CodeQL preflight has an invalid identity, branch or decision schema; do not PATCH."
      }
      if ($preflight.inspection_complete -is [bool] -and $preflight.inspection_complete -and
          $preflight.decision -cin @("may-offer-default-setup", "require-explicit-switch-confirmation")) {
        if ($preflight.advanced_workflows -isnot [Array] -or
            @($preflight.advanced_workflows | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
            $preflight.has_codeql_analysis -isnot [bool] -or
            $preflight.workflow_inspection_performed -isnot [bool] -or -not $preflight.workflow_inspection_performed -or
            $preflight.analysis_inspection_performed -isnot [bool] -or -not $preflight.analysis_inspection_performed -or
            $preflight.administration_permission -isnot [bool] -or -not $preflight.administration_permission -or
            $preflight.github_actions_enabled -isnot [bool] -or -not $preflight.github_actions_enabled -or
            $preflight.remote_inspection_commit -isnot [string] -or
            $preflight.remote_inspection_commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
          throw "CodeQL mutation evidence is incomplete or malformed; do not offer or cache it as an approved plan."
        }
      }
      if ($preflight.inspection_complete -isnot [bool] -or
          -not $preflight.inspection_complete) {
        Write-Warning "CodeQL setup inspection is inconclusive; do not PATCH."
      } elseif (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
          [string]$preflight.repository, "OWNER/REPO"
        ) -or [string]$preflight.default_branch -cne $DEFAULT_BRANCH) {
        throw "CodeQL preflight result is not bound to OWNER/REPO and DEFAULT_BRANCH; do not PATCH."
      } elseif ($preflight.decision -ne "preserve-default-setup" -and
          ($preflight.administration_permission -ne $true -or
           $preflight.github_actions_enabled -ne $true)) {
        Write-Warning "CodeQL default setup lacks confirmed administration permission or enabled Actions; do not PATCH."
      } elseif ($preflight.decision -eq "preserve-default-setup") {
        Write-Output "Code scanning default setup is already configured; preserve it without PATCHing."
      } elseif ($preflight.decision -eq "require-explicit-switch-confirmation") {
        $workflowSummary = @($preflight.advanced_workflows) -join ", "
        Write-Warning "Possible CodeQL advanced setup detected ($workflowSummary). Stop before PATCH, explain that default setup will disable the existing workflow and block CodeQL analysis API uploads, and ask for explicit approval to switch."
      } elseif ($preflight.decision -eq "may-offer-default-setup") {
        Write-Output "Default setup is eligible to be offered. Obtain the user's explicit approval before setting `$defaultSetupEnablementApproved or PATCHing."
      } else {
        Write-Warning "CodeQL preflight returned an unknown decision; do not PATCH."
      }
      $initialCodeqlDecision = $null
      $initialAdvancedWorkflows = $null
      $initialHasCodeqlAnalysis = $null
      $initialRemoteCodeqlCommit = $null
      if ($preflight.inspection_complete -is [bool] -and $preflight.inspection_complete) {
        $initialCodeqlDecision = $preflight.decision
        if ($preflight.decision -cin @("may-offer-default-setup", "require-explicit-switch-confirmation")) {
          $initialAdvancedWorkflows = @($preflight.advanced_workflows)
          $initialHasCodeqlAnalysis = $preflight.has_codeql_analysis
          $initialRemoteCodeqlCommit = $preflight.remote_inspection_commit
        }
      }
    }
  }
  ```

  Run the mutating block below only after the user explicitly approves enabling
  CodeQL default setup for this repository. General scaffold approval and the
  read-only `may-offer-default-setup` decision are not that approval. If the
  preflight reports `require-explicit-switch-confirmation`, explain the exact
  advanced workflows and analysis evidence, obtain separate approval to switch,
  and set `$defaultSetupSwitchApproved = $true` only then. Immediately before the
  PATCH, rerun the same preflight with the same target, branch, and explicit
  no-external-uploader confirmation; require the same decision and evidence,
  exact repository/default-branch binding, `not-configured` state, and current
  administration permission. A changed decision or evidence requires a new
review and approval. Also bind the final `remote_inspection_commit` to the
initial proof; a changed branch head invalidates absence evidence even when the
branch name and existing workflow list are unchanged. The PATCH can return `202 Accepted` with a validation
  workflow; wait for it to complete and require a final GET with
  `state: configured` before calling the feature enabled. A non-successful
  validation conclusion does not negate `state: configured`; report that
  conclusion separately and do not claim that scans succeeded:

  ```powershell
  if ($defaultSetupEnablementApproved -isnot [bool] -or -not $defaultSetupEnablementApproved) {
    throw "CodeQL default setup was not explicitly approved; do not PATCH."
  }
  if ($initialCodeqlDecision -eq "preserve-default-setup" -or
      $initialCodeqlDecision -notin @("may-offer-default-setup", "require-explicit-switch-confirmation")) {
    throw "The initial CodeQL preflight did not permit this mutation."
  }
  if ($initialCodeqlDecision -eq "require-explicit-switch-confirmation" -and
      ($defaultSetupSwitchApproved -isnot [bool] -or -not $defaultSetupSwitchApproved)) {
    throw "Switching from existing CodeQL setup was not separately approved; do not PATCH."
  }
  if ($noExternalCodeqlConfirmed -isnot [bool] -or -not $noExternalCodeqlConfirmed) {
    throw "Absence of external or indirect CodeQL uploaders was not confirmed; do not PATCH."
  }
  $finalPreflightOutput = & $pythonCommand.Source $preflightScript `
    --repo-root $REPO_ROOT `
    --repository "OWNER/REPO" `
    --expected-repository-id $SELECTED_REPOSITORY_ID `
    --default-branch $DEFAULT_BRANCH `
    --hostname "github.com" `
    --confirm-no-external-codeql `
    --require-administration-permission 2>&1
  $finalPreflightExitCode = $LASTEXITCODE
  if ($finalPreflightExitCode -ne 0) {
    throw "Final CodeQL preflight is inconclusive; do not PATCH. $($finalPreflightOutput | Out-String)"
  }
  try {
    $finalPreflightText = $finalPreflightOutput | Out-String
    if (-not $finalPreflightText.TrimStart().StartsWith("{")) { throw "Preflight must be an object." }
    $finalPreflight = $finalPreflightText | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Final CodeQL preflight returned invalid JSON; do not PATCH."
  }
  Assert-SelectedRepositoryId -RepositoryId $finalPreflight.repository_id
  if ($finalPreflight -isnot [pscustomobject] -or
      $finalPreflight.repository -isnot [string] -or $finalPreflight.default_branch -isnot [string] -or
      $finalPreflight.decision -isnot [string] -or $finalPreflight.default_setup_state -isnot [string] -or
      $finalPreflight.advanced_workflows -isnot [Array] -or
      @($finalPreflight.advanced_workflows | Where-Object { $_ -isnot [string] }).Count -gt 0 -or
      $finalPreflight.has_codeql_analysis -isnot [bool] -or
      $finalPreflight.administration_permission -isnot [bool] -or
      $finalPreflight.github_actions_enabled -isnot [bool] -or
      $finalPreflight.external_codeql_absence_confirmed -isnot [bool] -or
      $finalPreflight.workflow_inspection_performed -isnot [bool] -or -not $finalPreflight.workflow_inspection_performed -or
      $finalPreflight.analysis_inspection_performed -isnot [bool] -or -not $finalPreflight.analysis_inspection_performed -or
      $finalPreflight.remote_inspection_commit -isnot [string] -or
      $finalPreflight.remote_inspection_commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') {
    throw "Final CodeQL mutation evidence is incomplete or malformed; do not PATCH."
  }
  $finalAdvancedWorkflows = @($finalPreflight.advanced_workflows)
  if ($finalPreflight.inspection_complete -isnot [bool] -or
      -not $finalPreflight.inspection_complete -or
      $finalPreflight.decision -cne $initialCodeqlDecision -or
      $finalPreflight.default_setup_state -cne "not-configured" -or
      $finalPreflight.external_codeql_absence_confirmed -ne $true -or
      $finalPreflight.administration_permission -ne $true -or
      $finalPreflight.github_actions_enabled -ne $true -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$finalPreflight.repository, "OWNER/REPO"
      ) -or [string]$finalPreflight.default_branch -cne $DEFAULT_BRANCH -or
      $finalPreflight.has_codeql_analysis -ne $initialHasCodeqlAnalysis -or
      $finalPreflight.remote_inspection_commit -isnot [string] -or
      $finalPreflight.remote_inspection_commit -cne $initialRemoteCodeqlCommit -or
      $finalAdvancedWorkflows.Count -ne $initialAdvancedWorkflows.Count -or
      $null -ne (Compare-Object -ReferenceObject $initialAdvancedWorkflows -DifferenceObject $finalAdvancedWorkflows -CaseSensitive)) {
    throw "CodeQL evidence or target changed since approval; review the fresh result and obtain any required approval again."
  }
  if ($finalPreflight.decision -eq "require-explicit-switch-confirmation" -and
      ($defaultSetupSwitchApproved -isnot [bool] -or -not $defaultSetupSwitchApproved)) {
    throw "The fresh CodeQL result requires separate approval to switch; do not PATCH."
  }
  $defaultSetupPath = "repos/OWNER/REPO/code-scanning/default-setup"
  $defaultSetupEnabled = $false
  $observedDefaultSetupState = $null
  $validationFinished = $false
  $validationPollingFailed = $false
  $validationConclusion = $null
  $validationRunId = $null
  $setupOutput = & gh api --hostname github.com --include -X PATCH $defaultSetupPath -f state=configured 2>&1
  $setupExitCode = $LASTEXITCODE
  try {
    if ($setupExitCode -ne 0) {
      throw "Default-setup PATCH did not acknowledge success. State may have changed; do not replay or roll back automatically."
    }
    $setupText = $setupOutput | Out-String
    if ([Text.Encoding]::UTF8.GetByteCount($setupText) -gt 2097152 -or
        $setupText -notmatch '(?s)\AHTTP/[0-9]+(?:\.[0-9]+)? (200|202)[^\r\n]*\r?\n.*?\r?\n\r?\n(?<body>.*)\z') {
      throw "Default-setup acknowledgment has an invalid or oversized HTTP envelope."
    }
    $setupStatus = $Matches[1]
    $setupBody = $Matches['body'].Trim()
    $setupResponse = $null
    if ($setupBody.Length -gt 0) {
      $setupResponse = $setupBody | ConvertFrom-Json -ErrorAction Stop
      if ($setupResponse -isnot [pscustomobject]) {
        throw "Default-setup acknowledgment must be a JSON object."
      }
    }
    if ($null -ne $setupResponse -and $null -ne $setupResponse.run_id) {
      if (($setupResponse.run_id -isnot [int] -and $setupResponse.run_id -isnot [long]) -or
          $setupResponse.run_id -le 0) {
        throw "Default-setup validation run_id must be a positive JSON integer."
      }
      $validationRunId = $setupResponse.run_id
      # One bounded polling batch; retain the exact ID for later observation.
      for ($attempt = 0; $attempt -lt 4; $attempt++) {
        $runOutput = gh api --hostname github.com "repos/OWNER/REPO/actions/runs/$validationRunId" 2>&1
        if ($LASTEXITCODE -ne 0) {
          throw "Could not query the recorded default-setup validation run."
        }
        $runText = $runOutput | Out-String
        if ([Text.Encoding]::UTF8.GetByteCount($runText) -gt 2097152) {
          throw "Default-setup validation response exceeds the inspection limit."
        }
        $run = $runText | ConvertFrom-Json -ErrorAction Stop
        if ($run -isnot [pscustomobject] -or
            ($run.id -isnot [int] -and $run.id -isnot [long]) -or
            $run.id -ne $validationRunId -or
            $run.repository -isnot [pscustomobject] -or
            $run.repository.full_name -isnot [string] -or
            -not [StringComparer]::OrdinalIgnoreCase.Equals($run.repository.full_name, "OWNER/REPO")) {
          throw "Validation run is not bound to the recorded run ID and selected repository."
        }
        Assert-SelectedRepositoryId -RepositoryId $run.repository.id
        if ($run.status -isnot [string] -or $run.status -cnotin @(
            "queued", "in_progress", "completed", "waiting", "requested", "pending")) {
          throw "Default-setup validation status is unknown or malformed."
        }
        if ($run.status -ceq "completed") {
          if ($run.conclusion -isnot [string] -or $run.conclusion -cnotin @(
              "success", "failure", "neutral", "cancelled", "timed_out", "action_required", "stale", "skipped", "startup_failure")) {
            throw "Completed validation run has no recognized conclusion."
          }
          $validationFinished = $true
          $validationConclusion = $run.conclusion
          break
        }
        if ($null -ne $run.conclusion) {
          throw "Nonterminal validation run has a contradictory conclusion."
        }
        if ($attempt -lt 3) { Start-Sleep -Seconds 10 }
      }
      if (-not $validationFinished) {
        Write-Warning "Validation run $validationRunId is nonterminal. Preserve this ID and resume observation; do not repeat PATCH or claim successful validation."
      }
    } elseif ($setupStatus -ceq "200") {
      $validationFinished = $true
    } else {
      throw "An asynchronous 202 acknowledgment requires a positive validation run_id."
    }
  } catch {
    $validationPollingFailed = $true
    Write-Warning "Default-setup acknowledgment or validation is unverified. $($_.Exception.Message)"
  } finally {
    # Observe current state after every attempt, including failed acknowledgment or polling.
    # Bracket the state read because its response has no immutable repository identity.
    try {
      $beforeOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) { throw "Repository identity is unavailable before readback." }
      $before = ($beforeOutput | Out-String) | ConvertFrom-Json -ErrorAction Stop
      Assert-SelectedRepositoryId -RepositoryId $before.id
      if ($before.full_name -isnot [string] -or
          -not [StringComparer]::OrdinalIgnoreCase.Equals($before.full_name, "OWNER/REPO") -or
          $before.default_branch -isnot [string] -or $before.default_branch -cne $DEFAULT_BRANCH) {
        throw "Repository name or default branch changed before readback."
      }
      $verifyOutput = gh api --hostname github.com $defaultSetupPath 2>&1
      if ($LASTEXITCODE -ne 0) { throw "Final default-setup state could not be queried." }
      $verifyText = $verifyOutput | Out-String
      if ([Text.Encoding]::UTF8.GetByteCount($verifyText) -gt 2097152) {
        throw "Default-setup readback exceeds the inspection limit."
      }
      $verifiedSetup = $verifyText | ConvertFrom-Json -ErrorAction Stop
      if ($verifiedSetup -isnot [pscustomobject] -or $verifiedSetup.state -isnot [string] -or
          $verifiedSetup.state -cnotin @("configured", "not-configured")) {
        throw "Default-setup readback has an unknown or malformed state."
      }
      $afterOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
      if ($LASTEXITCODE -ne 0) { throw "Repository identity is unavailable after readback." }
      $after = ($afterOutput | Out-String) | ConvertFrom-Json -ErrorAction Stop
      Assert-SelectedRepositoryId -RepositoryId $after.id
      if ($after.full_name -isnot [string] -or
          -not [StringComparer]::OrdinalIgnoreCase.Equals($after.full_name, "OWNER/REPO") -or
          $after.default_branch -isnot [string] -or $after.default_branch -cne $DEFAULT_BRANCH) {
        throw "Repository name or default branch changed after readback."
      }
      $observedDefaultSetupState = $verifiedSetup.state
      Write-Output "Observed default-setup state: $observedDefaultSetupState. This observation is separate from acknowledgment and scan success."
      $defaultSetupEnabled = $observedDefaultSetupState -ceq "configured" -and
        $validationFinished -and -not $validationPollingFailed
    } catch {
      Write-Warning "Post-attempt default-setup state is unverified. $($_.Exception.Message)"
    }
  }
  if ($null -ne $validationConclusion -and $validationConclusion -cne "success") {
    Write-Warning "Default-setup validation concluded '$validationConclusion'; report this separately and do not claim successful scans."
  }
  if ($defaultSetupEnabled) {
    Write-Output "Code scanning default setup is enabled and verified."
  }
  ```

  When the bounded batch reports nonterminal validation, retain the typed `$validationRunId` and return progress. A later observation must repeat the exact run-ID and numeric repository-ID checks above, then the bracketed default-setup readback regardless of conclusion. Do not replay PATCH to obtain another run. Always observe state after a failed, ambiguous, malformed, or unsuccessful acknowledgment, without treating a readback as proof of request attribution or successful validation. A configured observation with pending or unverified validation is reported as current configuration only, not as completed enablement verification. Named REST reads cannot eliminate an intervening replacement race; the identity bracketing detects observed drift but is not an atomic guarantee.

- **Private vulnerability reporting**: despite its name, this repository setting is for receiving reports privately on a public repository. Offer it for a verified public repository, including a fork, with current administration permission. GitHub's [availability contract](https://docs.github.com/en/code-security/concepts/vulnerability-reporting-and-management/repository-security-advisories) does not impose a blanket non-fork restriction. Keep the explicit requested-feature approval and post-attempt verification below; preflight eligibility does not prove that an API write succeeded:

  ```powershell
  if ($enablePrivateVulnerabilityReportingRequested) {
    $featurePreflight = Get-ValidatedSecurityFeaturePreflight `
      -FeatureArguments @("--enable-private-vulnerability-reporting") `
      -ExpectedFeature "private_vulnerability_reporting"
    if ($null -ne $featurePreflight) {
      $enableOutput = gh api --hostname github.com -X PUT `
        repos/OWNER/REPO/private-vulnerability-reporting 2>&1
      $enableExitCode = $LASTEXITCODE
      if ($enableExitCode -ne 0) {
        Write-Warning "Private vulnerability reporting write did not acknowledge success; state may have changed. Inspect readback and the preserved response. $($enableOutput | Out-String)"
      }
      $verifyOutput = gh api --hostname github.com `
        repos/OWNER/REPO/private-vulnerability-reporting 2>&1
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "Private vulnerability reporting could not be verified after the attempted write. $($verifyOutput | Out-String)"
      } else {
        try { $reportingState = ($verifyOutput | Out-String) | ConvertFrom-Json } catch { $reportingState = $null }
        if ($null -eq $reportingState -or $reportingState.enabled -isnot [bool] -or
            $reportingState.enabled -ne $true) {
          Write-Warning "Private vulnerability reporting is not verified as enabled after the attempted write."
        }
      }
    }
  }
  ```

- **Dependency review workflow**: before installing
  `assets/workflows/dependency-review.yml`, run the bundled preflight. It proves
  that the dependency graph returns a bounded, structurally complete SPDX 2.x
  API envelope, including creation metadata and typed package records. It does
  not certify complete dependency resolution, current vulnerability results,
  license conformance, or the contents of a future repository revision. Document
  names need not equal the repository slug; empty package arrays and optional
  package versions are valid. Public repositories may proceed
  only with that proof; private or internal repositories must additionally be
  organization-owned and have GitHub Code Security enabled. A missing or
  malformed response is inconclusive and forbids installation. Run the
  workflow-installation preflight as well, because it independently checks the
  Actions policy and exact action pin.

  The shared GitHub client bounds retained stdout/stderr during capture, before
  temporary-file writes, and checks the remaining inspection-total byte budget
  before starting another CLI command. It requires both pipe EOFs and process
  completion; an oversized, timed-out or incomplete spool is not a verdict.
  Forced terminal output, paging and debug logging are disabled for that child
  invocation without changing the user's global CLI configuration. The owned
  CLI process is reaped and its parent-side pipes close on refusal. This does
  not establish SDK-internal memory/network limits, descendant cleanup, initial
  HTTP origin or the number of underlying requests made by `gh`.

  Gh-backed verdicts expose `github_client_requests` as client request attempts.
  The old `github_api_requests` field is retained only as a compatibility alias,
  tagged `github_api_requests_unit: client-request-attempts`; it is not an exact
  network count. `github_http_requests` is null with
  `github_http_requests_state: not-measured`. Unknown transport work is not zero,
  not-applicable, or successful quota evidence. Do not use these diagnostic
  fields in place of target, state, provenance or mutation-authorization checks.

  **SBOM API compatibility boundary**: the current helper uses the synchronous
  [`dependency-graph/sbom` export](https://docs.github.com/en/rest/dependency-graph/sboms#export-a-software-bill-of-materials-sbom-for-a-repository).
  It requires GitHub CLI's reported HTTP `200` status and a validated SPDX
  envelope; `201`, `202`, `204`, `206` or a redirect receipt cannot substitute
  for a completed export. This checks the observable response, not an
  attestation of the transport's initial response or redirect origin.
  GitHub has deprecated this endpoint and says it will be unavailable after
  November 13, 2026. The helper does not implement the async replacement.
  An unavailable or malformed export remains inconclusive and blocks copying
  the workflow; do not label the required inspection not-applicable or passed.

  Async migration requires separate authorization for the job-producing
  `generate-report` GET, exact repository/report binding, and bounded resume
  of the same returned report rather than repeated generation. Generation
  returns `201`; a fetch returns empty `202` while pending and `302` when ready.
  The report may be retained for up to one week, while each temporary download
  URL expires separately. These lifetimes are not verdict freshness or proof
  about a future repository revision. Do not forward GitHub credentials to the
  download destination or treat a generated-report acknowledgment as successful
  download/validation. No async operation is performed by the commands below.
  Each temporary URL's expiry is set when the fetch request is made; a
  migration must validate report and download-URL freshness separately.
  A documentation review date does not certify that the migration is implemented.

  ```powershell
  $dependencyReviewPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/dependency_review_preflight.py"
  if (-not (Test-Path -LiteralPath $dependencyReviewPreflight -PathType Leaf)) {
    throw "The bundled dependency-review preflight is missing; do not copy the workflow."
  }
  $dependencyReviewPreflightOutput = python $dependencyReviewPreflight `
    --repository "OWNER/REPO" --hostname "github.com" `
    --expected-repository-id $SELECTED_REPOSITORY_ID 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Dependency-review inspection is inconclusive; do not copy the workflow. $($dependencyReviewPreflightOutput | Out-String)"
  }
  $dependencyReviewPreflightText = $dependencyReviewPreflightOutput | Out-String
  if (-not $dependencyReviewPreflightText.TrimStart().StartsWith("{")) {
    throw "Dependency-review preflight must return a JSON object, not an array or scalar."
  }
  $dependencyReviewPreflightResult = $dependencyReviewPreflightText | ConvertFrom-Json
  if ($dependencyReviewPreflightResult -isnot [System.Management.Automation.PSCustomObject]) {
    throw "Dependency-review preflight returned a non-object verdict; do not copy the workflow."
  }
  Assert-SelectedRepositoryId -RepositoryId $dependencyReviewPreflightResult.repository_id
  if ($dependencyReviewPreflightResult.inspection_complete -isnot [bool] -or
      -not $dependencyReviewPreflightResult.inspection_complete -or
      $dependencyReviewPreflightResult.dependency_graph_available -isnot [bool] -or
      -not $dependencyReviewPreflightResult.dependency_graph_available -or
      $dependencyReviewPreflightResult.visibility -isnot [string] -or
      $dependencyReviewPreflightResult.visibility -notin @("public", "private", "internal") -or
      $dependencyReviewPreflightResult.github_code_security -isnot [string] -or
      ($dependencyReviewPreflightResult.visibility -eq "public" -and
       $dependencyReviewPreflightResult.github_code_security -ne "not-required") -or
      ($dependencyReviewPreflightResult.visibility -ne "public" -and
       $dependencyReviewPreflightResult.github_code_security -ne "enabled") -or
      $dependencyReviewPreflightResult.decision -isnot [string] -or
      $dependencyReviewPreflightResult.decision -ne "may-install-dependency-review-workflow") {
    throw "Dependency-review capability is not confirmed. Resolve the returned decision and rerun before copying the workflow."
  }
  if ($dependencyReviewPreflightResult.repository -isnot [string] -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
    [string]$dependencyReviewPreflightResult.repository, "OWNER/REPO"
  )) {
    throw "Dependency-review preflight returned a different repository; do not copy the workflow."
  }
  ```

  The v5 asset handles both `pull_request` and `merge_group` payloads. Require
  its `dependency-review` check only when the workflow can run on every event
  required by the repository's effective rules.

## Merge settings

Match the squash-default PR flow and keep branches tidy:

On GitHub.com, auto-merge is available for private repositories only with GitHub Pro, Team, or Enterprise Cloud. Check the repository plan/capability first. Treat `403` as forbidden; preserve a `422` response and report it as a rejected or ineligible configuration without guessing that token permission caused it. Do not install an auto-merge workflow unless enablement succeeds.

Run the bundled `scripts/merge_settings_preflight.py` before the first
merge-setting mutation. It is read-only and fail-closed: it binds the requested
repository response to the explicit `OWNER/REPO`, rejects archived or disabled
repositories, requires current administration permission, and rejects unbounded
effective-rule results. It preserves every method required by an effective merge
queue or pull-request rule, and records the exact resulting method plan. It
also binds the requested `delete_branch_on_merge=true` and
`squash_merge_commit_title=PR_TITLE` values and records their current state.
The flow reruns it before each separate settings write and compares the fresh
policy and current values with the expected intermediate state.
Within each inspection, it rereads the complete effective-rule evidence and any
classic-protection evidence used for auto-merge applicability, rejecting drift
even when a changed context list still contains checks. It then rereads numeric
repository identity, active/admin/default-branch state and the controlling merge
settings before returning the verdict. Requested deletion/title values are
included in that revalidation only when requested; uninspected optional values
remain explicitly null, not verified.
returns `require-explicit-merge-method-removal-confirmation` when the proposed
squash-default configuration would disable an enabled merge or rebase method.
Do not pass its confirmation flag until the user separately approves those named
removals. When `--require-auto-merge-workflows` reports
`skip-auto-merge-workflows`, preserve the merge settings plan but do not install
either shipped auto-merge asset. When it reports
`enable-auto-merge-before-installing-workflows`, do not install either asset:
enable the repository capability only with separate approval, verify the
mutation, then rerun the preflight.
When it reports `require-status-checks-before-installing-auto-merge-workflows`,
do not install either asset: configure at least one effective required status
check as a separate approved branch-policy change, verify it, then rerun the
preflight. The helper accepts required checks from an applicable ruleset or
classic protection, and fails closed when either API response is malformed.

```powershell
$mergeSettingsPreflight = Join-Path $REPO_SCAFFOLD_SKILL_ROOT "scripts/merge_settings_preflight.py"
if (-not (Test-Path -LiteralPath $mergeSettingsPreflight -PathType Leaf)) {
  throw "The bundled merge-settings preflight is missing; do not mutate merge settings."
}
if ([string]::IsNullOrWhiteSpace($DEFAULT_BRANCH)) {
  throw "DEFAULT_BRANCH must be known before inspecting merge settings."
}
$preflightArguments = @(
  "--repository", "OWNER/REPO",
  "--default-branch", $DEFAULT_BRANCH,
  "--require-auto-merge-workflows",
  "--expected-repository-id", $SELECTED_REPOSITORY_ID,
  "--enable-delete-branch-on-merge",
  "--squash-merge-commit-title", "PR_TITLE"
)
function Assert-MergePreflightSchema {
  param([Parameter(Mandatory)][object]$Verdict)

  if ($Verdict -isnot [pscustomobject]) {
    throw "Merge-settings preflight must return one JSON object; do not mutate."
  }
  Assert-SelectedRepositoryId -RepositoryId $Verdict.repository_id
  foreach ($field in @("inspection_complete", "administration_permission", "auto_merge_enabled", "auto_merge_workflows_eligible", "merge_queue_applies", "ruleset_status_checks_required")) {
    if ($Verdict.$field -isnot [bool]) {
      throw "Merge-settings preflight has an invalid Boolean $field; do not mutate."
    }
  }
  foreach ($field in @("classic_status_checks_required", "status_checks_required")) {
    if ($null -eq $Verdict.PSObject.Properties[$field] -or
        ($null -ne $Verdict.$field -and $Verdict.$field -isnot [bool])) {
      throw "Merge-settings preflight has invalid optional check evidence $field; do not mutate."
    }
  }
  foreach ($field in @("repository", "default_branch", "decision")) {
    if ($Verdict.$field -isnot [string] -or [string]::IsNullOrWhiteSpace($Verdict.$field)) {
      throw "Merge-settings preflight has an invalid $field; do not mutate."
    }
  }
  foreach ($field in @("current_merge_methods", "desired_merge_methods")) {
    if ($Verdict.$field -isnot [pscustomobject]) {
      throw "Merge-settings preflight has an invalid $field mapping; do not mutate."
    }
    foreach ($method in @("squash", "merge", "rebase")) {
      if ($Verdict.$field.$method -isnot [bool]) {
        throw "Merge-settings preflight has an invalid $field.$method; do not mutate."
      }
    }
  }
  foreach ($field in @("current_settings", "requested_settings")) {
    if ($Verdict.$field -isnot [pscustomobject] -or
        $Verdict.$field.delete_branch_on_merge -isnot [bool] -or
        $Verdict.$field.squash_merge_commit_title -isnot [string] -or
        $Verdict.$field.squash_merge_commit_title -cnotin @("PR_TITLE", "COMMIT_OR_PR_TITLE")) {
      throw "Merge-settings preflight did not inspect the requested $field; do not mutate."
    }
  }
  foreach ($field in @("required_merge_methods", "methods_to_disable")) {
    if ($Verdict.$field -isnot [Array] -or @($Verdict.$field).Count -gt 3 -or
        @($Verdict.$field | Where-Object {
          $_ -isnot [string] -or $_ -cnotin @("squash", "merge", "rebase")
        }).Count -gt 0 -or
        @($Verdict.$field | Select-Object -Unique).Count -ne @($Verdict.$field).Count) {
      throw "Merge-settings preflight has an invalid $field inventory; do not mutate."
    }
  }
}

$preflightOutput = python $mergeSettingsPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Merge-settings inspection is inconclusive; do not mutate. $($preflightOutput | Out-String)"
}
$mergeSettingsPreflightResult = ($preflightOutput | Out-String) | ConvertFrom-Json
Assert-MergePreflightSchema -Verdict $mergeSettingsPreflightResult
if ($mergeSettingsPreflightResult.inspection_complete -isnot [bool] -or
    -not $mergeSettingsPreflightResult.inspection_complete) {
  throw "Merge-settings inspection is incomplete; do not mutate."
}
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
  [string]$mergeSettingsPreflightResult.repository, "OWNER/REPO"
) -or [string]$mergeSettingsPreflightResult.default_branch -cne $DEFAULT_BRANCH) {
  throw "Merge-settings preflight result is not bound to OWNER/REPO and DEFAULT_BRANCH; do not mutate."
}
if ($mergeSettingsPreflightResult.requested_settings.delete_branch_on_merge -ne $true -or
    $mergeSettingsPreflightResult.requested_settings.squash_merge_commit_title -cne "PR_TITLE") {
  throw "Merge-settings preflight did not bind the requested branch-deletion and squash-title settings; do not mutate."
}
if ($mergeSettingsPreflightResult.decision -eq "require-explicit-merge-method-removal-confirmation") {
  $methods = @($mergeSettingsPreflightResult.methods_to_disable) -join ", "
  throw "Disabling enabled merge methods ($methods) needs separate user confirmation; do not mutate."
}
if ($mergeSettingsPreflightResult.decision -eq "require-status-checks-before-installing-auto-merge-workflows") {
  throw "No effective required status check gates auto-merge. Configure and verify branch policy before installing auto-merge workflows."
}
if ($mergeSettingsPreflightResult.decision -notin @(
  "may-configure-merge-settings", "skip-auto-merge-workflows",
  "enable-auto-merge-before-installing-workflows"
)) {
  throw "Merge-settings preflight returned an unknown decision; do not mutate."
}
$enableMergeCommit = [bool]$mergeSettingsPreflightResult.desired_merge_methods.merge
$enableRebaseMerge = [bool]$mergeSettingsPreflightResult.desired_merge_methods.rebase
$hasMergeQueue = [bool]$mergeSettingsPreflightResult.merge_queue_applies
$installAutoMergeWorkflows = [bool]$mergeSettingsPreflightResult.auto_merge_workflows_eligible
# Set only after the user explicitly requests one of the shipped assets and
# separately approves enabling this optional repository capability.
$installAutoMergeAssetsRequested = $false
$autoMergeCapabilityEnableApproved = $false
```

After separate approval for listed removals, append
`--confirm-disable-merge-methods`, rerun the preflight, and require the
`may-configure-merge-settings`, `skip-auto-merge-workflows`, or
`enable-auto-merge-before-installing-workflows` decision again. If the last
decision requires auto-merge enablement or required status checks, do not copy
an auto-merge asset until the separately approved mutation succeeds, its final
state is verified, and a rerun reports `may-configure-merge-settings`.
Use `$enableMergeCommit`, `$enableRebaseMerge`, and
`$installAutoMergeWorkflows` only from its final JSON result. The detailed
schema check must run on every initial, final, per-setting and post-mutation
verdict before casts or policy comparison. Explicit nullable status-check
evidence is preserved where the producer did not inspect classic checks or
where a merge queue makes the optional auto-merge checks inapplicable; missing
properties are not equivalent to explicit `null`. The consumer above requests
both branch deletion and squash-title settings, so both must be inspected.
The detailed
effective-rule inspection below is retained to explain the underlying GitHub
policy fields; do not replace the helper's result with manually inferred values.
The repository-level auto-merge capability is opt-in. Set
`$installAutoMergeAssetsRequested = $true` only after the user chooses one of the
two assets. If the preflight reports
`enable-auto-merge-before-installing-workflows`, set
`$autoMergeCapabilityEnableApproved = $true` only after separate approval to
change that repository setting. Without both conditions, leave the setting as
observed and do not install either asset.

For explanation and troubleshooting only, the following inspection shows the
effective-rule fields evaluated by the helper. The built-in `GITHUB_TOKEN`
cannot add a pull request to a merge queue, and the shipped workflows are
intentionally not queue workflows:

```powershell
$repoViewOutput = gh repo view github.com/OWNER/REPO --json nameWithOwner,defaultBranchRef
if ($LASTEXITCODE -ne 0) { throw "Failed to read repository metadata." }
$repoView = $repoViewOutput | ConvertFrom-Json
$owner, $repo = $repoView.nameWithOwner -split '/', 2
$defaultBranch = $repoView.defaultBranchRef.name
if ([string]::IsNullOrWhiteSpace($defaultBranch)) { throw "No default branch to inspect." }
$encodedBranch = [Uri]::EscapeDataString($defaultBranch)

$effectiveRuleOutput = gh api --hostname github.com --paginate --slurp `
  "repos/$owner/$repo/rules/branches/${encodedBranch}?per_page=100" 2>&1
$effectiveRuleExitCode = $LASTEXITCODE
if ($effectiveRuleExitCode -ne 0) {
  throw "Could not inspect effective merge rules; do not change merge methods or install auto-merge workflows. $($effectiveRuleOutput | Out-String)"
}
try {
  $effectiveRulePages = (($effectiveRuleOutput | Out-String) | ConvertFrom-Json)
  $effectiveRules = @(
    foreach ($page in @($effectiveRulePages)) {
      foreach ($rule in @($page)) { $rule }
    }
  )
} catch {
  throw "Effective branch rules returned invalid JSON; do not change merge methods or install auto-merge workflows."
}

$supportedMergeMethods = @("merge", "squash", "rebase")
$requiredRepositoryMergeMethods = [Collections.Generic.HashSet[string]]::new(
  [StringComparer]::OrdinalIgnoreCase
)
$mergeQueueRules = @($effectiveRules | Where-Object { $_.type -eq "merge_queue" })
$pullRequestRules = @($effectiveRules | Where-Object { $_.type -eq "pull_request" })

foreach ($rule in $mergeQueueRules) {
  $mergeMethod = [string]$rule.parameters.merge_method
  if ([string]::IsNullOrWhiteSpace($mergeMethod) -or
      $supportedMergeMethods -notcontains $mergeMethod.ToLowerInvariant()) {
    throw "An effective merge queue has a missing or unsupported merge method; preserve repository merge settings."
  }
  [void]$requiredRepositoryMergeMethods.Add($mergeMethod.ToLowerInvariant())
}
foreach ($rule in $pullRequestRules) {
  $allowedMergeMethods = @($rule.parameters.allowed_merge_methods)
  if ($allowedMergeMethods.Count -eq 0 -or
      @($allowedMergeMethods | Where-Object {
        $_ -isnot [string] -or $supportedMergeMethods -notcontains $_.ToLowerInvariant()
      }).Count -gt 0) {
    throw "An effective pull-request rule has missing or unsupported allowed merge methods; preserve repository merge settings."
  }
  foreach ($mergeMethod in $allowedMergeMethods) {
    [void]$requiredRepositoryMergeMethods.Add($mergeMethod.ToLowerInvariant())
  }
}

$hasMergeQueue = $mergeQueueRules.Count -gt 0
if ($hasMergeQueue) {
  Write-Warning "An effective merge queue rule applies. Skip repo-scaffold auto-merge workflows; design queue automation with a confirmed PAT or GitHub App token instead."
}
```

Continue with the repository merge settings below even when `$hasMergeQueue` is true, but preserve every merge method used by an effective merge queue or allowed by an effective pull-request rule. Install `auto-merge.yml` or `dependabot-auto-merge.yml` only when `$installAutoMergeWorkflows` from the final preflight is true.

```powershell
# Values come only from the final merge-settings preflight. A queue configured
# for MERGE or REBASE must keep that method enabled or GitHub will block it.
$initialMergePlan = [ordered]@{
  repository = [string]$mergeSettingsPreflightResult.repository
  default_branch = [string]$mergeSettingsPreflightResult.default_branch
  decision = [string]$mergeSettingsPreflightResult.decision
  desired_merge_methods = $mergeSettingsPreflightResult.desired_merge_methods
  methods_to_disable = @($mergeSettingsPreflightResult.methods_to_disable)
  merge_queue_applies = [bool]$mergeSettingsPreflightResult.merge_queue_applies
  auto_merge_enabled = [bool]$mergeSettingsPreflightResult.auto_merge_enabled
  auto_merge_workflows_eligible = [bool]$mergeSettingsPreflightResult.auto_merge_workflows_eligible
  status_checks_required = $mergeSettingsPreflightResult.status_checks_required
  requested_settings = $mergeSettingsPreflightResult.requested_settings
  current_settings = $mergeSettingsPreflightResult.current_settings
} | ConvertTo-Json -Depth 6 -Compress
$finalPreflightOutput = python $mergeSettingsPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Final merge-settings inspection is inconclusive; do not mutate. $($finalPreflightOutput | Out-String)"
}
$finalMergePreflight = ($finalPreflightOutput | Out-String) | ConvertFrom-Json
Assert-MergePreflightSchema -Verdict $finalMergePreflight
if ($finalMergePreflight.inspection_complete -isnot [bool] -or
    -not $finalMergePreflight.inspection_complete -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      [string]$finalMergePreflight.repository, "OWNER/REPO"
    ) -or [string]$finalMergePreflight.default_branch -cne $DEFAULT_BRANCH) {
  throw "Final merge-settings preflight is incomplete or bound to another target; do not mutate."
}
$finalMergePlan = [ordered]@{
  repository = [string]$finalMergePreflight.repository
  default_branch = [string]$finalMergePreflight.default_branch
  decision = [string]$finalMergePreflight.decision
  desired_merge_methods = $finalMergePreflight.desired_merge_methods
  methods_to_disable = @($finalMergePreflight.methods_to_disable)
  merge_queue_applies = [bool]$finalMergePreflight.merge_queue_applies
  auto_merge_enabled = [bool]$finalMergePreflight.auto_merge_enabled
  auto_merge_workflows_eligible = [bool]$finalMergePreflight.auto_merge_workflows_eligible
  status_checks_required = $finalMergePreflight.status_checks_required
  requested_settings = $finalMergePreflight.requested_settings
  current_settings = $finalMergePreflight.current_settings
} | ConvertTo-Json -Depth 6 -Compress
if ($initialMergePlan -cne $finalMergePlan -or
    $finalMergePreflight.decision -notin @(
      "may-configure-merge-settings", "skip-auto-merge-workflows",
      "enable-auto-merge-before-installing-workflows"
    )) {
  throw "Merge settings or effective rules changed since approval; review the fresh preflight before mutating."
}
$mergeSettingsPreflightResult = $finalMergePreflight
$enableMergeCommit = [bool]$finalMergePreflight.desired_merge_methods.merge
$enableRebaseMerge = [bool]$finalMergePreflight.desired_merge_methods.rebase
$hasMergeQueue = [bool]$finalMergePreflight.merge_queue_applies
$installAutoMergeWorkflows = [bool]$finalMergePreflight.auto_merge_workflows_eligible
if ($installAutoMergeAssetsRequested -isnot [bool] -or
    $autoMergeCapabilityEnableApproved -isnot [bool]) {
  throw "Auto-merge asset choice and capability approval must be Boolean values; do not infer consent."
}
$enableAutoMergeNow = -not [bool]$finalMergePreflight.auto_merge_enabled -and
  $installAutoMergeAssetsRequested -and $autoMergeCapabilityEnableApproved
$expectedAutoMergeEnabled = [bool]$finalMergePreflight.auto_merge_enabled -or $enableAutoMergeNow

function Get-ValidatedFreshMergePreflight {
  param(
    [Parameter(Mandatory)][bool]$ExpectedAutoMergeEnabled,
    [Parameter(Mandatory)][bool]$ExpectedDeleteBranchOnMerge,
    [Parameter(Mandatory)][string]$ExpectedSquashMergeCommitTitle,
    [switch]$RequireDesiredMergeMethods
  )

  $output = python $mergeSettingsPreflight @preflightArguments 2>&1
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) {
    throw "Fresh merge-settings preflight is inconclusive; do not mutate. $($output | Out-String)"
  }
  try {
    $result = ($output | Out-String) | ConvertFrom-Json
  } catch {
    throw "Fresh merge-settings preflight returned invalid JSON; do not mutate."
  }
  Assert-MergePreflightSchema -Verdict $result
  $policy = [ordered]@{
    required_merge_methods = @($result.required_merge_methods)
    desired_merge_methods = $result.desired_merge_methods
    merge_queue_applies = [bool]$result.merge_queue_applies
    ruleset_status_checks_required = $result.ruleset_status_checks_required
    classic_status_checks_required = $result.classic_status_checks_required
    status_checks_required = $result.status_checks_required
  } | ConvertTo-Json -Depth 6 -Compress
  $approvedPolicy = [ordered]@{
    required_merge_methods = @($finalMergePreflight.required_merge_methods)
    desired_merge_methods = $finalMergePreflight.desired_merge_methods
    merge_queue_applies = [bool]$finalMergePreflight.merge_queue_applies
    ruleset_status_checks_required = $finalMergePreflight.ruleset_status_checks_required
    classic_status_checks_required = $finalMergePreflight.classic_status_checks_required
    status_checks_required = $finalMergePreflight.status_checks_required
  } | ConvertTo-Json -Depth 6 -Compress
  if ($result.inspection_complete -isnot [bool] -or
      -not $result.inspection_complete -or
      -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [string]$result.repository, "OWNER/REPO"
      ) -or [string]$result.default_branch -cne $DEFAULT_BRANCH -or
      $result.administration_permission -ne $true -or
      $result.requested_settings.delete_branch_on_merge -ne $true -or
      $result.requested_settings.squash_merge_commit_title -cne "PR_TITLE" -or
      [bool]$result.auto_merge_enabled -ne $ExpectedAutoMergeEnabled -or
      [bool]$result.current_settings.delete_branch_on_merge -ne $ExpectedDeleteBranchOnMerge -or
      [string]$result.current_settings.squash_merge_commit_title -cne $ExpectedSquashMergeCommitTitle -or
      $policy -cne $approvedPolicy -or
      $result.decision -notin @(
        "may-configure-merge-settings", "skip-auto-merge-workflows",
        "enable-auto-merge-before-installing-workflows"
      )) {
    throw "Fresh merge policy, repository settings, or request changed; review it again before mutating."
  }
  if ($RequireDesiredMergeMethods -and
      ([bool]$result.current_merge_methods.squash -ne [bool]$result.desired_merge_methods.squash -or
       [bool]$result.current_merge_methods.merge -ne [bool]$result.desired_merge_methods.merge -or
       [bool]$result.current_merge_methods.rebase -ne [bool]$result.desired_merge_methods.rebase)) {
    throw "Fresh merge-method state does not match the approved plan; do not continue mutating."
  }
  return $result
}

$mergeArguments = @(
  "repo", "edit", "github.com/OWNER/REPO",
  "--enable-squash-merge=true",
  "--enable-merge-commit=$($enableMergeCommit.ToString().ToLowerInvariant())",
  "--enable-rebase-merge=$($enableRebaseMerge.ToString().ToLowerInvariant())",
  "--delete-branch-on-merge"
)
$completedMergeUpdates = @()
$mergeSettingsFailure = $null
try {
  $mergeOutput = & gh @mergeArguments 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure merge methods. $($mergeOutput | Out-String)"
  }
  $completedMergeUpdates += "merge_methods_and_branch_cleanup"

  $null = Get-ValidatedFreshMergePreflight `
    -ExpectedAutoMergeEnabled ([bool]$finalMergePreflight.auto_merge_enabled) `
    -ExpectedDeleteBranchOnMerge $true `
    -ExpectedSquashMergeCommitTitle ([string]$finalMergePreflight.current_settings.squash_merge_commit_title) `
    -RequireDesiredMergeMethods
  $squashTitleOutput = & gh api --hostname github.com -X PATCH `
    repos/OWNER/REPO -f squash_merge_commit_title=PR_TITLE 2>&1
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the squash commit title. $($squashTitleOutput | Out-String)"
  }
  $completedMergeUpdates += "squash_merge_commit_title"

  if ($enableAutoMergeNow) {
    $null = Get-ValidatedFreshMergePreflight `
      -ExpectedAutoMergeEnabled $false `
      -ExpectedDeleteBranchOnMerge $true `
      -ExpectedSquashMergeCommitTitle "PR_TITLE" `
      -RequireDesiredMergeMethods
    $autoMergeOutput = & gh repo edit github.com/OWNER/REPO --enable-auto-merge 2>&1
    if ($LASTEXITCODE -ne 0) {
      $autoMergeError = $autoMergeOutput | Out-String
      if ($autoMergeError -match '(?is)HTTP 403') {
        throw "GitHub forbade auto-merge enablement. Verify repository plan and Administration permission. Do not install auto-merge workflows. $autoMergeError"
      }
      if ($autoMergeError -match '(?is)HTTP 404') {
        throw "The repository is missing or inaccessible. Re-verify repository identity; do not install auto-merge workflows. $autoMergeError"
      }
      if ($autoMergeError -match '(?is)HTTP 422') {
        throw "GitHub rejected auto-merge enablement as invalid, ineligible, or abuse-limited. Inspect the preserved response; do not install auto-merge workflows. $autoMergeError"
      }
      throw "Failed to enable auto-merge. $autoMergeError"
    }
    $completedMergeUpdates += "auto_merge"
  }
} catch {
  $mergeSettingsFailure = $_.Exception.Message
}

# These settings are changed by separate API operations. Re-read every requested
# field even after a failure so partial state is visible and cannot be reported as
# an all-or-nothing success.
$finalMergeOutput = gh api --hostname github.com repos/OWNER/REPO 2>&1
if ($LASTEXITCODE -ne 0) {
  if ($null -ne $mergeSettingsFailure) {
    throw "$mergeSettingsFailure The update sequence is non-atomic and final merge settings could not be read. Confirmed successful calls: $($completedMergeUpdates -join ', ')."
  }
  throw "Merge-setting mutations returned success, but the final state could not be verified. $($finalMergeOutput | Out-String)"
}
$finalMergeSettings = ($finalMergeOutput | Out-String) | ConvertFrom-Json
Assert-SelectedRepositoryId -RepositoryId $finalMergeSettings.id
if ($finalMergeSettings.full_name -isnot [string] -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      $finalMergeSettings.full_name, "OWNER/REPO"
    )) {
  throw "Final merge-setting readback is not bound to OWNER/REPO; state remains unverified."
}
foreach ($setting in @("allow_squash_merge", "allow_merge_commit", "allow_rebase_merge", "delete_branch_on_merge", "allow_auto_merge")) {
  if ($finalMergeSettings.$setting -isnot [bool]) {
    throw "Final merge-setting readback has an invalid Boolean $setting; state remains unverified."
  }
}
if ($finalMergeSettings.squash_merge_commit_title -isnot [string]) {
  throw "Final merge-setting readback has an invalid squash commit title policy; state remains unverified."
}
$finalMergeSummary = [ordered]@{
  allow_squash_merge = [bool]$finalMergeSettings.allow_squash_merge
  allow_merge_commit = [bool]$finalMergeSettings.allow_merge_commit
  allow_rebase_merge = [bool]$finalMergeSettings.allow_rebase_merge
  delete_branch_on_merge = [bool]$finalMergeSettings.delete_branch_on_merge
  allow_auto_merge = [bool]$finalMergeSettings.allow_auto_merge
  squash_merge_commit_title = [string]$finalMergeSettings.squash_merge_commit_title
} | ConvertTo-Json -Compress
if ($null -ne $mergeSettingsFailure) {
  throw "$mergeSettingsFailure The non-atomic sequence may be partially applied. Confirmed successful calls: $($completedMergeUpdates -join ', '). Final state: $finalMergeSummary"
}

$mergeSettingProblems = @()
if (-not [bool]$finalMergeSettings.allow_squash_merge) {
  $mergeSettingProblems += "squash merge is disabled"
}
if ([bool]$finalMergeSettings.allow_merge_commit -ne $enableMergeCommit) {
  $mergeSettingProblems += "merge-commit setting differs from effective-rule requirements"
}
if ([bool]$finalMergeSettings.allow_rebase_merge -ne $enableRebaseMerge) {
  $mergeSettingProblems += "rebase setting differs from effective-rule requirements"
}
if (-not [bool]$finalMergeSettings.delete_branch_on_merge) {
  $mergeSettingProblems += "head-branch deletion is disabled"
}
if ([bool]$finalMergeSettings.allow_auto_merge -ne $expectedAutoMergeEnabled) {
  $mergeSettingProblems += "auto-merge setting differs from the explicitly approved target"
}
if ([string]$finalMergeSettings.squash_merge_commit_title -cne "PR_TITLE") {
  $mergeSettingProblems += "squash commit title is not PR_TITLE"
}
if ($mergeSettingProblems.Count -gt 0) {
  throw "Repository merge settings did not reach the requested state: $($mergeSettingProblems -join '; '). Final state: $finalMergeSummary"
}
$postMergePreflightOutput = python $mergeSettingsPreflight @preflightArguments 2>&1
if ($LASTEXITCODE -ne 0) {
  throw "Merge settings were updated, but the final auto-merge eligibility check is inconclusive; do not install either workflow. $($postMergePreflightOutput | Out-String)"
}
$postMergePreflight = ($postMergePreflightOutput | Out-String) | ConvertFrom-Json
Assert-MergePreflightSchema -Verdict $postMergePreflight
if ($postMergePreflight.inspection_complete -isnot [bool] -or
    -not $postMergePreflight.inspection_complete -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
      [string]$postMergePreflight.repository, "OWNER/REPO"
    ) -or [string]$postMergePreflight.default_branch -cne $DEFAULT_BRANCH -or
    [bool]$postMergePreflight.auto_merge_enabled -ne [bool]$finalMergeSettings.allow_auto_merge -or
    [bool]$finalMergeSettings.allow_squash_merge -ne [bool]$postMergePreflight.desired_merge_methods.squash -or
    [bool]$finalMergeSettings.allow_merge_commit -ne [bool]$postMergePreflight.desired_merge_methods.merge -or
    [bool]$finalMergeSettings.allow_rebase_merge -ne [bool]$postMergePreflight.desired_merge_methods.rebase -or
    [bool]$finalMergeSettings.delete_branch_on_merge -ne [bool]$postMergePreflight.current_settings.delete_branch_on_merge -or
    [string]$finalMergeSettings.squash_merge_commit_title -cne [string]$postMergePreflight.current_settings.squash_merge_commit_title) {
  throw "Merge settings or effective branch rules changed during configuration; review the fresh result and do not install either workflow."
}
$installAutoMergeWorkflows = [bool]$postMergePreflight.auto_merge_workflows_eligible
if ($installAutoMergeAssetsRequested -and -not $installAutoMergeWorkflows) {
  Write-Warning "Fresh merge-settings preflight returned '$($postMergePreflight.decision)'; do not install either auto-merge workflow."
}
```

Run the exact workflow-installation preflight for the selected asset first. Then
immediately before copying each selected auto-merge asset, rerun the merge
preflight against the same approved settings and effective-rule evidence:

```powershell
if ($installAutoMergeAssetsRequested -isnot [bool] -or $installAutoMergeWorkflows -isnot [bool]) {
  throw "Auto-merge copy choice or eligibility is malformed; do not copy the asset."
}
if ($installAutoMergeAssetsRequested -and $installAutoMergeWorkflows) {
  $copyMergePreflight = Get-ValidatedFreshMergePreflight `
    -ExpectedAutoMergeEnabled $true `
    -ExpectedDeleteBranchOnMerge $true `
    -ExpectedSquashMergeCommitTitle "PR_TITLE" `
    -RequireDesiredMergeMethods
  if (-not $copyMergePreflight.auto_merge_workflows_eligible) {
    throw "Fresh merge-settings preflight no longer permits this asset; do not copy it."
  }
}
```

Repeat this check before the second asset if both were selected. A change in the
default branch, effective queue/rules, required-check eligibility, or repository
settings means the old installation verdict cannot authorize a later copy.

The repository API setting `squash_merge_commit_title=PR_TITLE` makes the final squash commit use the PR title. When `commitlint.yml` is installed, its dependency-free `commitlint` job validates that title as well as the PR commits, preserving Conventional Commit input for release-please. `--enable-auto-merge` is required for any auto-merge workflow (`gh pr merge --auto`) to work — both Dependabot auto-merge and the label-gated `auto-merge.yml`.

## release-please token (RELEASE_PLEASE_TOKEN)

Before copying `release.yml`, `release-please.yml`, or an attestation-enabled
release variant, run the bundled release preflight from the installed skill
root. It verifies the exact GitHub.com repository identity, rejects archived or
disabled repositories, binds the installation to the remote default branch,
and chooses the allowed attestation variant. Do not infer private or internal
repository attestation eligibility from visibility alone: GitHub requires
Enterprise Cloud for those repositories, so use `--github-enterprise-cloud`
only after separately confirming that plan.

```bash
python "$REPO_SCAFFOLD_SKILL_ROOT/scripts/release_preflight.py" \
  --repository OWNER/REPO \
  --expected-repository-id "$SELECTED_REPOSITORY_ID" \
  --default-branch DEFAULT_BRANCH \
  --with-attestations
```

Proceed with attestation-enabled assets only when the JSON decision is
`may-install-attestation-workflows`. When it returns
`render-no-attestation-variant`, install the documented no-attestation variant
instead. A result of `inconclusive` forbids the release workflow mutation until
the evidence is repaired. For a confirmed private or internal GitHub Enterprise
Cloud repository, add `--github-enterprise-cloud`; omit
`--with-attestations` when provenance attestations are not requested.
Before copying any release asset, require the same result to report
`repository` equal to `OWNER/REPO` (case-insensitive) and `default_branch`
exactly equal to `DEFAULT_BRANCH`. Rerun the preflight immediately before
copying if the selected repository or default branch changed.
Require the typed `repository_id` to equal the original positive numeric
`SELECTED_REPOSITORY_ID`; an unbound discovery-only decision cannot authorize
any attestation or no-attestation asset. Secret-name inspection and other
multi-request eligibility checks revalidate identity and controlling repository
state before their final verdict. Any drift is inconclusive, not a valid
no-attestation fallback. This does not grant release publication authority or
prove that a secret's value has the required scopes.

Treat plugin-creator's local `+codex.<cachebuster>` suffix as installation identity only. Do not copy it into the public release manifest, plugin version, changelog, or tag; confirm and use the clean public SemVer instead. Preserve other SemVer build metadata only when the user explicitly confirms it is part of the public release identity.

The shipped `release.yml` also supports a verified manual recovery path
without a `push.tags` trigger. Run it only after the exact tag exists and
resolves to the supplied full commit SHA. All three privileged release jobs
accept only three caller routes: a direct `workflow_dispatch` of `release.yml`
from the default branch, `release-please.yml` calling it on a default-branch
push, or `release-tag.yml` calling it for the pushed `v*` tag with matching tag
and commit inputs. Other workflow-call files, events, and refs are blocked
before build, OIDC, or publication permissions are granted. GitHub associates
the reusable workflow's `github` context with its caller, so the gate binds the
event, ref, and caller file
([reusable workflow documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations)).
The release workflow definition still comes from the tag ref for tag-triggered
releases, so only trusted tag creators may use that path. See GitHub's
[workflow ref semantics](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)
and the [push-event SHA definition](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).

```bash
gh workflow run release.yml --repo OWNER/REPO --ref DEFAULT_BRANCH \
  -f tag=vX.Y.Z -f commit_sha=FULL_COMMIT_SHA
```

Only when the repo ships `release-please.yml` (or `auto-merge.yml`, which prefers this token). Use a fine-grained PAT so release-please-created PRs can trigger their required checks and merges performed by the label-gated workflow can trigger the default-branch release-please run; events created by the default `GITHUB_TOKEN` do not start another workflow run.

`auto-merge.yml` may read this PAT only from its trusted-base `pull_request_target` workflow after the human-user and same-repository guards pass. Never change that workflow to `pull_request` while it references the PAT, and never add a checkout, fetch, artifact download, or command that executes PR-controlled content.

Use these exact Release Please locale values so repeated scaffolds remain
deterministic:

| Locale | Pull-request title | Header | Footer lead-in |
| --- | --- | --- | --- |
| `en` | `chore${scope}: release${component} ${version}` | `:robot: I have created a release *beep* *boop*` | `This PR was generated with` |
| `vi` | `chore${scope}: phát hành${component} ${version}` | `:robot: Release Please đã tạo PR phát hành tự động này.` | `PR này được tạo tự động bằng` |

For `en`, use `Features`, `Bug Fixes`, `Performance Improvements`, `Reverts`,
`Documentation`, `Styles`, `Miscellaneous Chores`, `Code Refactoring`, `Tests`,
`Build System`, and `Continuous Integration`. For `vi`, use `Tính năng`,
`Sửa lỗi`, `Cải thiện hiệu năng`, `Hoàn tác`, `Tài liệu`, `Định dạng mã`,
`Bảo trì khác`, `Tái cấu trúc mã`, `Kiểm thử`, `Hệ thống xây dựng`, and
`Tích hợp liên tục`. Keep the repeated `feat` and `feature` entries and every
`hidden` flag from the asset unchanged. Complete the footer with the same
Release Please and documentation links from the English asset, translating
only the surrounding prose.

When installing release-please, also copy `assets/release-please-config.json` and `assets/release-please-manifest.json` to the repository root as `release-please-config.json` and `.release-please-manifest.json`. Render the config's pull-request title, header, footer, and changelog section names in the resolved `SCAFFOLD_LANGUAGE`. Preserve `${scope}`, `${component}`, and `${version}` exactly in the title pattern, and preserve the asset's changelog type order and `hidden` flags so localization does not change release semantics. Before changing the title pattern in an existing setup, list open release PRs and coordinate the transition: release-please uses the configured pattern to build and parse titles, so rename an existing release PR to the exact new pattern immediately before the config lands, or wait until that PR is resolved. After the first run with the new config, verify that the original release PR was updated and no duplicate was opened. The config intentionally combines `draft: true` with `force-tag-creation: true`. release-please creates the tag before it creates the draft Release, so never pair this mode with `release-tag.yml` or another `push.tags: v*` caller: that caller could observe the tag before the draft exists. The shipped `release-please.yml` instead waits for the release-please action to complete, then invokes reusable `release.yml` with the emitted tag and the action's `sha` output. The engine serializes callers by tag. A read-only build job verifies the tag through the authenticated Git database references/tags REST APIs, checks out that immutable commit without persisted credentials, builds and validates regular-file artifacts, then transfers them through SHA-pinned artifact actions. For an eligible repository, a fresh attestation job downloads those files without a checkout, validates them without executing project code, and generates SLSA build provenance with `actions/attest`; it alone receives `id-token: write` and `attestations: write`, while the reusable-workflow caller passes those permissions through. GitHub currently supports attestations for public repositories on current plans and for private/internal repositories on GitHub Enterprise Cloud. For an ineligible repository, render the documented no-attestation variant rather than leaving a gate that cannot succeed. A separate write-enabled publish job on a fresh runner downloads but never executes those artifacts, waits for attestation when enabled, verifies the tag immediately before publishing, and checks it once more after publication. When it creates a missing draft Release, `--verify-tag` prevents GitHub CLI from silently recreating a tag that disappeared after verification. A pre-publication mismatch leaves the Release as a draft and fails. A post-publication mismatch is an integrity incident that the workflow reports but cannot roll back; use an effective tag ruleset or immutable releases when tag movement must be prevented rather than merely detected. Reruns may repair only drafts: they skip an existing asset only when its name, size, SHA-256 digest, and state match the expected artifact, and upload only missing assets without replacing existing ones. Unexpected or conflicting assets stop the run. The publisher refuses every published Release, including a legacy mutable one; create a new version tag instead. Fill the manifest with a confirmed current version without a leading `v`; do not invent an initial version. Do not remove either option or collapse the build, eligible attestation, and publish permission boundaries while `release.yml` is responsible for artifacts.

The release publisher bounds release-list inspection to 16 pages, re-reads the
tag's draft state before each upload and publication, compares the complete
uploaded asset names, sizes, and SHA-256 digests with the build outputs, and
verifies the published state. On a draft rerun, it skips only exact matching
assets and uploads only missing assets without replacing existing ones; an
unexpected or conflicting asset stops the run. It fails closed on an oversized
history or any state mismatch. GitHub's [REST guidance for conditional
requests](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
says unsafe requests are not conditional unless an endpoint documents that
support. Its [release asset upload endpoint](https://docs.github.com/en/rest/releases/assets)
documents a separate upload request and no draft-state precondition. The
resulting inference is that this flow cannot atomically bind the draft read to
the upload or publish write. It rechecks immediately before each request and
never replaces existing assets, but an external publisher can still race that
request window; keep release write access limited and enable immutable releases
when supported.

The shipped freshness registry treats `release-please-config.json` as optional: it skips the path until Release Please is installed, then audits its `$schema` on the next scheduled or manual freshness run. Do not remove that tracker entry. The same workflow includes the bundled CI-toolchain policy and checker, so its Markdown tooling pin receives the same reminder coverage.

After downloading a published artifact, verify both its provenance and the reusable signer workflow:

```bash
gh attestation verify PATH/TO/ARTIFACT \
  --repo OWNER/REPO \
  --signer-workflow OWNER/REPO/.github/workflows/release.yml
```

1. Create the PAT (GitHub UI → Settings → Developer settings → Fine-grained tokens, or `gh` if available). Scope it least-privilege: **only this repository**, permissions **Contents: Read and write** + **Pull requests: Read and write** (add **Issues: Read and write** if release-please manages issues). Nothing else.
2. Add it as a repository secret named **exactly** `RELEASE_PLEASE_TOKEN` (Settings → Secrets and variables → Actions → New repository secret). The name must match the `secrets.RELEASE_PLEASE_TOKEN` reference in the workflows character-for-character — secret names allow only letters, digits, and underscores (no hyphens/spaces), so a mismatch makes the action fail with "Input required: token".

3. Before installing `release-please.yml` or an `auto-merge.yml` that depends on
   the PAT, verify only the secret's exact repository-scoped name. This does not
   retrieve, print, or otherwise expose its value. An unavailable secret API or
   a missing/mismatched name is inconclusive and forbids that workflow mutation:

   ```bash
   python "$REPO_SCAFFOLD_SKILL_ROOT/scripts/release_preflight.py" \
     --repository OWNER/REPO \
     --expected-repository-id "$SELECTED_REPOSITORY_ID" \
     --default-branch DEFAULT_BRANCH \
     --require-release-please-token
   ```

   Continue only when `release_please_token` is `verified-present` and the JSON
   result reports the exact `OWNER/REPO` and `DEFAULT_BRANCH` inputs. The caller
   still must confirm that the PAT itself has the documented least-privilege
   scopes; GitHub's secret API intentionally cannot prove them.

Never paste the token value into a chat, commit, or log. If one is ever exposed, revoke it immediately and create a new one.

## Verify

Read visibility, fork state, and the default branch first:

```bash
gh repo view github.com/OWNER/REPO --json visibility,isFork,defaultBranchRef
```

- For any non-fork repository, verify community profile health. Public resources can be queried without authentication; private repositories require an authenticated token with `Contents: read`:

  ```bash
  gh api --hostname github.com repos/OWNER/REPO/community/profile --jq '.health_percentage'
  ```

- Skip the community-profile call only for forks. For a private non-fork repository, run it with authenticated `gh`; if it fails, report the permission/API error rather than claiming private repositories are unsupported.
- Confirm license detection:

  ```bash
  gh api --hostname github.com repos/OWNER/REPO/license --jq '.license.spdx_id'
  ```

- Verify the selected protection mechanism on the detected default branch. URL-encode the branch because valid branch names can contain `/`:

  ```powershell
  $repoViewOutput = gh repo view github.com/OWNER/REPO --json nameWithOwner,defaultBranchRef
  if ($LASTEXITCODE -ne 0) { throw "Failed to read repository metadata." }
  $repoView = $repoViewOutput | ConvertFrom-Json
  $owner, $repo = $repoView.nameWithOwner -split '/', 2
  $defaultBranch = $repoView.defaultBranchRef.name
  if ([string]::IsNullOrWhiteSpace($defaultBranch)) { throw "No default branch to verify." }
  $encodedBranch = [Uri]::EscapeDataString($defaultBranch)

  # Classic branch protection, when configured.
  gh api --hostname github.com "repos/$owner/$repo/branches/$encodedBranch/protection" `
    --jq '{pr: (.required_pull_request_reviews != null), admins: .enforce_admins.enabled, checks: ((.required_status_checks.checks // []) | map({context, app_id}))}'

  # Inspect effective active rulesets, including organization-level rulesets.
  gh api --hostname github.com --paginate "repos/$owner/$repo/rules/branches/$encodedBranch" `
    --jq '.[] | select(.type == "pull_request" or .type == "required_status_checks")'
  ```

Run the classic command when this plugin configured classic protection. Always run the effective-rules command to identify preserved repository or organization rulesets; this is inspection, not proof that the plugin configured them. For classic protection, compare every returned check `context` and `app_id` with the exact `$requiredCheckNames` and `$requiredAppIdsByContext` used during setup. For an effective ruleset `required_status_checks` rule, inspect its `context` and `integration_id` separately and report conflicts with the intended checks; never compare a ruleset field to classic `app_id`. Re-query both Check Runs and Commit Statuses on each representative PR's head and current test-merge SHAs, recent default-branch SHAs, and a merge-group SHA when applicable. The test-merge SHA controls when it has any Check Runs or Commit Statuses; otherwise the head SHA controls. Verify that each required context has exactly one effective job name and that its controlling-sha App ID matches the configured source. Fail verification if a same-name Commit Status exists on any controlling SHA. Remove, rename, or correct a classic context that no workflow emits, that multiple workflows emit, whose app binding differs, or that collides across the two status systems. Preserve existing rulesets unless the user starts a separate approved ruleset-policy change.
