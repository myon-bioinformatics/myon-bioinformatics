import { readFile, writeFile } from 'node:fs/promises';

const PROFILE_URL = 'https://raw.githubusercontent.com/myon-bioinformatics/myon-bioinformatics.github.io/main/profile.json';
const README_PATH = new URL('../README.md', import.meta.url);
const START = '<!-- PROFILE:START -->';
const END = '<!-- PROFILE:END -->';

const response = await fetch(PROFILE_URL, {
  headers: { 'user-agent': 'myon-profile-sync' }
});
if (!response.ok) {
  throw new Error(`Failed to fetch canonical profile: ${response.status} ${response.statusText}`);
}

const profile = await response.json();

const skills = profile.skills
  .map(group => `### ${group.category}\n${group.items.map(item => `- ${item.name} (${item.level}%)`).join('\n')}`)
  .join('\n\n');

const career = profile.career
  .map(item => `- **${item.year} — ${item.title}**: ${item.desc}`)
  .join('\n');

const focus = (profile.currentFocus ?? [])
  .map(item => `- ${item}`)
  .join('\n');

const links = profile.links ?? {};
const linkLine = [
  links.portfolio && `[Portfolio](${links.portfolio})`,
  links.github && `[GitHub](${links.github})`,
  links.twitter && `[Twitter](${links.twitter})`,
  links.litlink && `[lit.link](${links.litlink})`,
  links.linktree && `[Linktree](${links.linktree})`
].filter(Boolean).join(' • ');

const block = `${START}\n> **Canonical source:** [profile.json](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/main/profile.json)\n> This section is generated automatically. Edit the canonical JSON instead of this README block.\n\n## 👋 Summary / 自己紹介\n\n${profile.about.replace(/\n/g, '  \n')}\n\n> _“${profile.motto}”_\n\n## 🧭 Career Flow\n\n${career}\n\n## 🧰 Skills\n\n${skills}\n\n## 🧑‍💻 What I’m Working On\n\n${focus}\n\n## 📫 Contact\n\n${linkLine}\n${END}`;

const readme = await readFile(README_PATH, 'utf8');
const startIndex = readme.indexOf(START);
const endIndex = readme.indexOf(END);

if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
  throw new Error(`README must contain ${START} and ${END}`);
}

const updated = `${readme.slice(0, startIndex)}${block}${readme.slice(endIndex + END.length)}`;
await writeFile(README_PATH, updated);
