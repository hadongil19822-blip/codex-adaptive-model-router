# Contributing

Thanks for improving Codex Adaptive Model Router.

## Good first contributions

- Add task signals for another language.
- Improve model scoring without adding a model call.
- Add a new menu bar localization.
- Test a newer Codex release and update the compatibility notes.
- Add support for another operating system without weakening safety checks.

## Development

```bash
python3 -m unittest discover -s tests -v
./macos-widget/build-widget.sh
```

The Python router uses only the standard library. The optional menu app uses SwiftUI and requires macOS 13+ with Xcode Command Line Tools.

## Pull requests

1. Keep routing decisions transparent and deterministic.
2. Add tests for every scoring or safety change.
3. Do not commit rollout files, runtime state, prompts, credentials, build products, or machine-specific paths.
4. Document changes to private or experimental Codex interfaces.
5. Explain the token-cost and reliability tradeoff of new routes.

By contributing, you agree that your contribution is licensed under the MIT License.
