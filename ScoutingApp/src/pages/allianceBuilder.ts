// Compare hands its teams over as ?teams=frc1,frc2,frc3.
export function teamsFromParam(raw: string | null): [string, string, string] {
  const keys = String(raw || '')
    .split(',')
    .map((value) => value.trim().toLowerCase())
    .filter((value, index, all) => /^frc\d+$/.test(value) && all.indexOf(value) === index)
    .slice(0, 3);
  return [keys[0] || '', keys[1] || '', keys[2] || ''];
}
