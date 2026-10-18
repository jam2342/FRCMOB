// One way to write a time: "7:59 AM", and "Apr 30, 7:59 AM" when the day matters. The app had
// four ("07:59 AM", "~7:59 AM", and two full date-times with seconds).
export function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

export function formatDayTime(ms: number): string {
  return new Date(ms).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
