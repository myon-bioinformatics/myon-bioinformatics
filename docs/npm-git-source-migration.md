# npm Git distribution for shared Node tools

The parent repository is also a **minimal npm Git package**. It remains
`"private": true` to prevent accidental npm registry publication; this flag
does **not** prevent installation via an HTTPS Git dependency. Only
`py_launch.cjs` and LICENSE are distributed. Node >=24 is required.
The existing single-file `vendor/py_launch.cjs` usage is still supported.

For a child repository with `package.json`, add a **reviewed full commit SHA**
to its dependency map (substitute an actual 40-hex released parent commit):

```json
{
  "dependencies": {
    "@myon-bioinformatics/portable-tools": "git+https://github.com/myon-bioinformatics/myon-bioinformatics.git#<40-hex-commit>"
  }
}
```

Then **generate and commit** the child's `package-lock.json` once using
`npm install --package-lock-only`. CI must run `npm ci`, not an unconstrained
`npm install`; Git dependencies in the lock resolve to a specific commit.

```js
const pyLaunch = require("@myon-bioinformatics/portable-tools/py_launch");
// pyLaunch.run(scriptPath, argv, { noSite: true })
```

Do not add npm dependencies just to execute built-in Node functionality.
Do not replace direct imports of `vendor/py_launch.cjs` until each consumer
has updated its entry points, proven the same runtime/error contract, and
successfully run its complete CI. Keep the old vendor pin until that migration
is complete; afterwards remove that specific entry from `vendor.lock.json`
and its vendored bytes as a separate, reviewable change.

The parent `npm-git-package.yml` runs `npm ci`, packages the root through
`npm pack`, and installs that tarball into an unrelated temporary consumer
**offline**, verifying its exported API. It does not claim a live Git-hosted
install was tested. `uv.lock` is for the parent's Python test/dev environment;
`vendor.lock.json` still validates *unmigrated* raw copied files. This npm
metadata does not turn the parent's `pyproject.toml` into an installable
Python distribution.
