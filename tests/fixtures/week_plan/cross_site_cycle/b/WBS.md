# B

- [ ] Cycle
  - [ ] Waits on a1 {issue: Issue-1, est: 6h, h: 0, depends_on: [a:Issue-1]}
  - [ ] Downstream on b {issue: Issue-2, est: 6h, h: 0, depends_on: [Issue-1]}
  - [ ] Free on b {issue: Issue-3, est: 6h, h: 0}
  - [ ] Own cycle one {issue: Issue-4, est: 6h, h: 0, depends_on: [Issue-5]}
  - [ ] Own cycle two {issue: Issue-5, est: 6h, h: 0, depends_on: [Issue-4]}
- [ ] Row target {issue: Issue-6}
  - [ ] Under the target {issue: Issue-7, est: 6h, h: 0, depends_on: [a:Issue-5]}
