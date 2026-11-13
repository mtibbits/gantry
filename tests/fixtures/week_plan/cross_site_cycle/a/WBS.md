# A

- [ ] Cycle
  - [ ] Waits on a2 {issue: Issue-1, est: 6h, h: 0, depends_on: [Issue-2]}
  - [ ] Waits on b1 {issue: Issue-2, est: 6h, h: 0, depends_on: [b:Issue-1]}
  - [ ] Downstream on a {issue: Issue-3, est: 6h, h: 0, depends_on: [b:Issue-1]}
  - [ ] Free on a {issue: Issue-4, est: 6h, h: 0}
  - [ ] Waits on a row of b {issue: Issue-5, est: 6h, h: 0, depends_on: [b:Issue-6]}
