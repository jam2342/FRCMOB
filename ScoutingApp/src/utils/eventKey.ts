// What an event key looks like: the season, then a code that starts with a letter
// (2026arc, 2026nmrc2). "99999999" or "5417" typed into a search box is not one; taking it
// for an event loaded a page of errors, and the stored copy carried them to every page.
const EVENT_KEY_SHAPE = /^(19|20)\d{2}[a-z][a-z0-9]{1,15}$/;

export function isEventKeyShape(value: string): boolean {
  return EVENT_KEY_SHAPE.test(value.trim().toLowerCase());
}
