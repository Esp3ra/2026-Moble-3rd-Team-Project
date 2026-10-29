'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { relatedPullRequest, matchesReviewIssue, run } = require('./close-review-issues.cjs');
const repository = 'Esp3ra/2026-Moble-3rd-Team-Project';

function issue(number, pr = 7, labels = ['pr-review']) {
  return { number, state: 'open', labels, body: `### 관련 PR\n\nhttps://github.com/${repository}/pull/${pr}\n\n### 문제와 병합할 수 없는 이유\n\n문제 설명` };
}

function fixture({ merged = true, base = 'Alpha', issues = [], eventName = 'pull_request_target', inputs } = {}) {
  const writes = [];
  const pr = { number: 7, merged, base: { ref: base, repo: { full_name: repository } }, html_url: `https://github.com/${repository}/pull/7` };
  const github = {
    rest: {
      pulls: { get: async () => ({ data: pr }) },
      issues: { listForRepo() {}, createComment: async value => writes.push(['comment', value]), update: async value => writes.push(['update', value]) },
    },
    paginate: async () => issues,
  };
  const context = { eventName, repo: { owner: 'Esp3ra', repo: '2026-Moble-3rd-Team-Project' }, payload: { action: 'closed', pull_request: pr, inputs } };
  return { github, context, core: { info() {} }, writes };
}

test('form is valid YAML-compatible JSON with the automatic-close field and label', () => {
  const form = JSON.parse(fs.readFileSync('.github/ISSUE_TEMPLATE/pr_review.yml', 'utf8'));
  assert.ok(form.name.length > 3);
  assert.ok(form.labels.includes('pr-review'));
  const field = form.body.find(item => item.id === 'related_pr');
  assert.equal(field.attributes.label, '관련 PR');
  assert.equal(field.validations.required, true);
  assert.equal(new Set(form.body.filter(item => item.id).map(item => item.id)).size, form.body.filter(item => item.id).length);
});

test('read only the dedicated PR field, including CRLF and local URLs', () => {
  for (const value of ['#7', 'PR #7', '7', `https://github.com/${repository}/pull/7`]) {
    assert.equal(relatedPullRequest(`### 관련 PR\r\n\r\n${value}\r\n\r\n### 기타\r\n#99`, repository), 7);
  }
  for (const body of ['본문에서 #7 언급', '### 관련 PR\nhttps://github.com/someone/another/pull/7', '### 관련 PR\n#7\n\n### 관련 PR\n#8', '### 관련 PR\n#7, #8']) {
    assert.equal(relatedPullRequest(body, repository), null);
  }
});

test('never match other PRs, ordinary issues, closed issues or PR objects', () => {
  assert.equal(matchesReviewIssue(issue(1), 7, repository), true);
  for (const candidate of [issue(2, 8), issue(3, 7, ['bug']), { ...issue(4), state: 'closed' }, { ...issue(5), pull_request: {} }]) {
    assert.equal(matchesReviewIssue(candidate, 7, repository), false);
  }
});

test('closed without merge and normal PR events never close issues', async () => {
  for (const options of [{ merged: false }, { eventName: 'pull_request' }]) {
    const input = fixture({ ...options, issues: [issue(1)] });
    const result = await run(input);
    assert.equal(result.closed, 0);
    assert.equal(input.writes.length, 0);
  }
});

test('actual merge completes only linked review issues for every managed branch', async () => {
  for (const base of ['main', 'Alpha', 'Beta', 'Release']) {
    const input = fixture({ base, issues: [issue(1), issue(2, 8), issue(3, 7, ['bug']), { ...issue(4), state: 'closed' }] });
    assert.equal((await run(input)).closed, 1);
    const updates = input.writes.filter(([kind]) => kind === 'update').map(([, value]) => value);
    assert.deepEqual(updates, [{ owner: 'Esp3ra', repo: '2026-Moble-3rd-Team-Project', issue_number: 1, state: 'closed', state_reason: 'completed' }]);
  }
});

test('manual checks default to dry run and never write', async () => {
  const input = fixture({ eventName: 'workflow_dispatch', inputs: { pull_request_number: '7' }, issues: [issue(1)] });
  assert.deepEqual(await run(input), { pullRequest: 7, dryRun: true, matched: 1, closed: 0 });
  assert.equal(input.writes.length, 0);
});

test('manual completion requires explicit dry_run=false and valid inputs', async () => {
  const input = fixture({ eventName: 'workflow_dispatch', inputs: { pull_request_number: '7', dry_run: 'false' }, issues: [issue(1)] });
  assert.equal((await run(input)).closed, 1);
  const bad = fixture({ eventName: 'workflow_dispatch', inputs: { pull_request_number: '7; echo invalid', dry_run: 'false' } });
  await assert.rejects(() => run(bad), /positive PR number/);
  assert.equal(bad.writes.length, 0);
});

test('unsupported target branches cannot change issues', async () => {
  const input = fixture({ base: 'feature/someone-task', issues: [issue(1)] });
  await assert.rejects(() => run(input), /managed branch/);
  assert.equal(input.writes.length, 0);
});
