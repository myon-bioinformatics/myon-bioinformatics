import { readFile, writeFile } from 'node:fs/promises';

const CANONICAL_OWNER = 'myon-bioinformatics';
const CANONICAL_REPO = `${CANONICAL_OWNER}.github.io`;
const CANONICAL_RAW_BASE = `https://raw.githubusercontent.com/${CANONICAL_OWNER}/${CANONICAL_REPO}/main`;
const README_PATH = new URL('../README.md', import.meta.url);

const BLOCKS = {
  header: ['<!-- HEADER:START -->', '<!-- HEADER:END -->'],
  profile: ['<!-- PROFILE:START -->', '<!-- PROFILE:END -->'],
  projects: ['<!-- PROJECTS:START -->', '<!-- PROJECTS:END -->'],
  footer: ['<!-- FOOTER:START -->', '<!-- FOOTER:END -->'],
};

const fetchJson = async (path) => {
  const response = await fetch(`${CANONICAL_RAW_BASE}/${path}`, {
    headers: { 'user-agent': 'myon-readme-sync' },
  });
  if (!response.ok) {
    throw new Error(`Failed to fetch canonical ${path}: ${response.status} ${response.statusText}`);
  }
  return response.json();
};

const [profile, projects, repoPayload, registry] = await Promise.all([
  fetchJson('profile.json'),
  fetchJson('projects.json'),
  fetchJson('api/repos.json'),
  fetchJson('services.json'),
]);

const owner = registry.owner;
const services = registry.services ?? {};
const githubUrlRaw = services.account?.github?.url;
const portfolioUrlRaw = services.portfolio?.pages?.url;
const repos = new Map(repoPayload.repos.map((repo) => [repo.name, repo]));

if (!owner || !githubUrlRaw || !portfolioUrlRaw) {
  throw new Error('services.json must provide owner, account.github.url, and portfolio.pages.url');
}

const githubUrl = githubUrlRaw.replace(/\/+$/, '');
const portfolioUrl = `${portfolioUrlRaw.replace(/\/+$/, '')}/`;
const canonicalRepo = `${owner}.github.io`;
const canonicalRepoUrl = `${githubUrl}/${canonicalRepo}`;

const replaceBlock = (readme, [start, end], body) => {
  const startIndex = readme.indexOf(start);
  const endIndex = readme.indexOf(end);
  if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
    throw new Error(`README must contain ${start} and ${end}`);
  }
  return `${readme.slice(0, startIndex)}${start}\n${body.trim()}\n${end}${readme.slice(endIndex + end.length)}`;
};

const skills = profile.skills
  .map((group) => `### ${group.category}\n${group.items.map((item) => `- ${item.name} (${item.level}%)`).join('\n')}`)
  .join('\n\n');

const career = profile.career
  .map((item) => `- **${item.year} — ${item.title}**: ${item.desc}`)
  .join('\n');

const focus = (profile.currentFocus ?? []).map((item) => `- ${item}`).join('\n');
const links = profile.links ?? {};

const headerLinks = [
  links.litlink && `<a href="${links.litlink}">lit.link</a>`,
  links.linktree && `<a href="${links.linktree}">Linktree</a>`,
  `<a href="${portfolioUrl}">Portfolio (GitHub Pages)</a>`,
].filter(Boolean).join(' •\n  ');

const headerBlock = `<p align="center">\n  ${profile.headline}\n</p>\n\n<p align="center">\n  ${headerLinks}\n</p>`;

const contactLine = [
  `[Portfolio](${portfolioUrl})`,
  `[GitHub](${githubUrl})`,
  links.twitter && `[Twitter](${links.twitter})`,
  links.litlink && `[lit.link](${links.litlink})`,
  links.linktree && `[Linktree](${links.linktree})`,
].filter(Boolean).join(' • ');

const profileBlock = `> **Canonical source:** [profile.json](${canonicalRepoUrl}/blob/main/profile.json)\n> This section is generated automatically. Edit the canonical JSON instead of this README block.\n\n## 👋 Summary / 自己紹介\n\n${profile.about.replace(/\n/g, '  \n')}\n\n> _“${profile.motto}”_\n\n## 🧭 Career Flow\n\n${career}\n\n## 🧰 Skills\n\n${skills}\n\n## 🧑‍💻 What I’m Working On\n\n${focus}\n\n## 📫 Contact\n\n${contactLine}`;

const featured = projects.map((project) => {
  const repo = repos.get(project.name);
  if (!repo) throw new Error(`projects.json references unknown repository: ${project.name}`);
  return repo;
});

const pin = (repo) => `  <a href="${repo.url}">\n    <img src="https://github-readme-stats.vercel.app/api/pin/?username=${owner}&repo=${encodeURIComponent(repo.name)}&theme=vue-dark" />\n  </a>`;

const featuredRows = [];
for (let i = 0; i < featured.length; i += 2) {
  featuredRows.push(`<p align="left">\n${featured.slice(i, i + 2).map(pin).join('\n')}\n</p>`);
}

const featuredNames = new Set(featured.map((repo) => repo.name));
const moreRepos = repoPayload.repos
  .filter((repo) => repo.name !== owner && !featuredNames.has(repo.name))
  .sort((a, b) => a.name.localeCompare(b.name))
  .map((repo) => `* [${repo.name}](${repo.url})`)
  .join('\n');

const projectsBlock = `## 📦 Featured Repositories\n\n> **Selection source:** [projects.json](${canonicalRepoUrl}/blob/main/projects.json) · **Repository facts:** [api/repos.json](${portfolioUrl}api/repos.json)\n\n${featuredRows.join('\n')}\n\n<details>\n<summary>🔎 More repos</summary>\n\n${moreRepos}\n\n</details>`;

const footerBlock = `## ℹ️ More about me\n\n😏 **詳細はこちら** → <a href="${portfolioUrl}">${new URL(portfolioUrl).host}</a>`;

let readme = await readFile(README_PATH, 'utf8');
readme = replaceBlock(readme, BLOCKS.header, headerBlock);
readme = replaceBlock(readme, BLOCKS.profile, profileBlock);
readme = replaceBlock(readme, BLOCKS.projects, projectsBlock);
readme = replaceBlock(readme, BLOCKS.footer, footerBlock);
await writeFile(README_PATH, readme);
