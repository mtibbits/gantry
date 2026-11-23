# A

- [ ] Work
  - [ ] Waits on a leaf of c {issue: Issue-1, est: 10h, h: 2, depends_on: [c:Issue-1]}
  - [ ] Free {issue: Issue-2, est: 8h}
  - [ ] Behind the wait {issue: Issue-3, est: 4h, h: 1, depends_on: [Issue-1]}
  - [ ] In a cycle with c {issue: Issue-4, est: 4h, h: 1, depends_on: [c:Issue-6]}
  - [ ] In a cycle with b {issue: Issue-5, est: 4h, h: 1, depends_on: [b:Issue-3]}
