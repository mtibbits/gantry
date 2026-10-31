# Priority
- [ ] Base {issue: Issue-1, priority: P3}
- [ ] Middle {issue: Issue-2, priority: P3, depends_on: [Issue-1]}
- [ ] Top {issue: Issue-3, priority: P1, depends_on: [Issue-2]}
- [ ] Gate {issue: Issue-4, priority: P3}
- [x] Closed {issue: Issue-5, depends_on: [Issue-4]}
- [ ] Urgent {issue: Issue-6, priority: P1, depends_on: [Issue-5]}
- [ ] Far {issue: Issue-7, priority: P3}
- [ ] Remote {issue: Issue-8, priority: P1, depends_on: [beta:Issue-7]}
