// Deterministic derivatives of the existing, user-supplied brand artwork.
// Run on macOS (sips is included with the OS). No generated artwork or paid services.
import { execFileSync } from 'node:child_process';
import { copyFileSync, readdirSync, existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
const source = 'public/Heading.png';
const sizes = { mdpi: 48, hdpi: 72, xhdpi: 96, xxhdpi: 144, xxxhdpi: 192 };
function image(path, width, height = width, content = Math.min(width, height)) {
  execFileSync('sips', ['-z', String(content), String(content), source, '--out', path], { stdio: 'ignore' });
  if (content !== width || content !== height) execFileSync('sips', ['--padToHeightWidth', String(height), String(width), '--padColor', '292929', path], { stdio: 'ignore' });
}
for (const [density, size] of Object.entries(sizes)) {
  const folder = `android/app/src/main/res/mipmap-${density}`;
  for (const name of ['ic_launcher.png', 'ic_launcher_round.png']) image(join(folder, name), size);
  // Adaptive launchers mask the outer third; keep all lettering in the safe area.
  image(join(folder, 'ic_launcher_foreground.png'), Math.round(size * 2.25), undefined, Math.round(size * 1.35));
}
for (const folder of readdirSync('android/app/src/main/res').filter(name => name.startsWith('drawable'))) {
  const path = join('android/app/src/main/res', folder, 'splash.png');
  // Retain each generated resource's dimensions to preserve device qualifiers.
  if (!existsSync(path)) continue;
  {
    const info = execFileSync('sips', ['-g', 'pixelWidth', '-g', 'pixelHeight', path], { encoding: 'utf8' });
    const width = Number(info.match(/pixelWidth: (\d+)/)[1]); const height = Number(info.match(/pixelHeight: (\d+)/)[1]);
    image(path, width, height, Math.round(Math.min(width, height) * 0.38));
  }
}
copyFileSync(source, 'ios/App/App/Assets.xcassets/AppIcon.appiconset/AppIcon-512@2x.png');
for (const { filename } of JSON.parse(readFileSync('ios/App/App/Assets.xcassets/Splash.imageset/Contents.json')).images) {
  image(`ios/App/App/Assets.xcassets/Splash.imageset/${filename}`, 2732, 2732, 750);
}
// Android 12+ shows the adaptive app icon over the configured launch background.
console.log('Native icons and splash artwork generated from public/Heading.png.');
