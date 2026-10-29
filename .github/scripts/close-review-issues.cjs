'use strict';

const TARGET_BRANCHES = new Set(['main', 'Alpha', 'Beta', 'Release']);
const REVIEW_LABEL = 'pr-review';

function relatedPullRequest(body, repository) {
  const sections = String(body || '').replace(/\r\n/g, '\n').split(/^### /m);
  const matches = sections.filter(section => section.split('\n', 1)[0].trim() === '관련 PR');
  if (matches.length !== 1) return null;
  const field = matches[0].slice(matches[0].indexOf('\n') + 1).trim();
  const number = field.match(/^(?:PR\s+)?#?([1-9][0-9]*)$/i);
  if (number) return Number.isSafeInteger(Number(number[1])) ? Number(number[1]) : null;
  try {
    const url = new URL(field);
    const parts = url.pathname.replace(/\/$/, '').split('/').slice(1);
    if (url.protocol !== 'https:' || url.hostname.toLowerCase() !== 'github.com' || parts.length !== 4) return null;
    if (`${parts[0]}/${parts[1]}`.toLowerCase() !== repository.toLowerCase() || parts[2] !== 'pull') return null;
    return /^[1-9][0-9]*$/.test(parts[3]) && Number.isSafeInteger(Number(parts[3])) ? Number(parts[3]) : null;
  } catch {
    return null;
  }
}

function matchesReviewIssue(issue, prNumber, repository) {
  const labels = (issue.labels || []).map(label => typeof label === 'string' ? label : label.name);
  return issue.state === 'open' && !issue.pull_request && labels.includes(REVIEW_LABEL)
    && relatedPullRequest(issue.body, repository) === prNumber;
}

async function run({ github, context, core }) {
  const { owner, repo } = context.repo;
  const repository = `${owner}/${repo}`;
  let pr;
  let dryRun = false;
  if (context.eventName === 'workflow_dispatch') {
    const input = String(context.payload.inputs?.pull_request_number || '');
    if (!/^[1-9][0-9]*$/.test(input) || !Number.isSafeInteger(Number(input))) throw new Error('A positive PR number is required.');
    const dryInput = String(context.payload.inputs?.dry_run ?? 'true');
    if (!['true', 'false'].includes(dryInput)) throw new Error('dry_run must be true or false.');
    dryRun = dryInput === 'true';
    ({ data: pr } = await github.rest.pulls.get({ owner, repo, pull_number: Number(input) }));
  } else if (context.eventName === 'pull_request_target' && context.payload.action === 'closed') {
    pr = context.payload.pull_request;
  } else {
    core.info('No supported merge event; no issues changed.');
    return { matched: 0, closed: 0 };
  }
  if (!pr?.merged) {
    core.info('PR is not merged; review issues stay open.');
    return { matched: 0, closed: 0, dryRun };
  }
  if (!TARGET_BRANCHES.has(pr.base?.ref) || pr.base?.repo?.full_name?.toLowerCase() !== repository.toLowerCase()) {
    throw new Error('The merged PR must target a managed branch in this repository.');
  }
  if (!Number.isSafeInteger(pr.number) || pr.number < 1) throw new Error('Invalid PR number.');
  const issues = await github.paginate(github.rest.issues.listForRepo, {
    owner, repo, state: 'open', labels: REVIEW_LABEL, per_page: 100,
  });
  const matches = issues.filter(issue => matchesReviewIssue(issue, pr.number, repository));
  for (const issue of matches) {
    core.info(`${dryRun ? 'Dry run' : 'Complete'}: issue #${issue.number} for PR #${pr.number}.`);
    if (dryRun) continue;
    await github.rest.issues.createComment({
      owner, repo, issue_number: issue.number,
      body: `관련 PR #${pr.number}이 ${pr.base.ref}에 병합되어 검토 수정 요청을 해결 완료로 종료합니다.\n\n${pr.html_url}`,
    });
    await github.rest.issues.update({ owner, repo, issue_number: issue.number, state: 'closed', state_reason: 'completed' });
  }
  core.info(`PR #${pr.number}: matched=${matches.length}, closed=${dryRun ? 0 : matches.length}, dryRun=${dryRun}.`);
  return { pullRequest: pr.number, dryRun, matched: matches.length, closed: dryRun ? 0 : matches.length };
}

module.exports = { relatedPullRequest, matchesReviewIssue, run };
