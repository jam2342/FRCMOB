import { describe, expect, it } from 'vitest';
import { encodeRoomKeyForQr, parseRoomKeyFromQr } from './qrUtils';

describe('parseRoomKeyFromQr', () => {
  it('round-trips encoded room payloads', () => {
    const payload = encodeRoomKeyForQr('Room-Encode_42');
    expect(payload).toBe('FRCROOM1:room-encode_42');
    expect(parseRoomKeyFromQr(payload)).toBe('room-encode_42');
  });

  it('parses plain room keys', () => {
    expect(parseRoomKeyFromQr('ROOM-ABC_123')).toBe('room-abc_123');
  });

  it('parses prefixed room qr payloads', () => {
    expect(parseRoomKeyFromQr('FRCROOM1:Room-Team-One')).toBe('room-team-one');
  });

  it('parses room keys from json payloads', () => {
    expect(parseRoomKeyFromQr('{"room_key":"room-json-1"}')).toBe('room-json-1');
  });

  it('parses room keys from urls', () => {
    expect(parseRoomKeyFromQr('https://example.com/scouting?room_key=room-url-1')).toBe('room-url-1');
    expect(parseRoomKeyFromQr('https://example.com/api/scouting/rooms/room-path-1/ws')).toBe('room-path-1');
  });

  it('does not treat scouting entry qr payloads as room keys', () => {
    expect(parseRoomKeyFromQr('FRCMOB1:abcd1234')).toBeNull();
  });

  it('returns null for unrelated text', () => {
    expect(parseRoomKeyFromQr('this is not a room')).toBeNull();
  });
});
