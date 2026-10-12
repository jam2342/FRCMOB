import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PushNotificationsCard } from './PushNotificationsCard';
import { getPushPublicKey } from '../../api';
vi.mock('../../api', () => ({getPushPublicKey:vi.fn(),subscribePush:vi.fn(async () => ({})),unsubscribePush:vi.fn(),sendPushTest:vi.fn()}));
vi.mock('../../platform/runtime', () => ({isNativeApp: () => false}));
afterEach(() => {cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();});
function support(subscribed = false) {
  vi.stubGlobal('PushManager', class {});
  vi.stubGlobal('Notification', {permission:'granted'});
  vi.stubGlobal('navigator', {serviceWorker:{ready:Promise.resolve({pushManager:{getSubscription:async () => subscribed ? {endpoint:'https://push.example/device',toJSON: () => ({keys:{p256dh:'test',auth:'test'}}),unsubscribe: async () => true} : null}})}});
}

it('allows clearing and replacing lead time, then clamps on blur', async () => {
  support();
  vi.mocked(getPushPublicKey).mockResolvedValue({configured:true,public_key:'abc'} as never);
  render(<PushNotificationsCard />);
  const input = await screen.findByRole('spinbutton', { name: 'Lead time (minutes before match)' });
  fireEvent.change(input, { target: { value: '' } });
  expect(input).toHaveValue(null);
  fireEvent.change(input, { target: { value: '25' } });
  fireEvent.blur(input);
  expect(input).toHaveValue(25);
  fireEvent.change(input, { target: { value: '99' } });
  fireEvent.blur(input);
  expect(input).toHaveValue(60);
  fireEvent.change(input, { target: { value: '' } });
  fireEvent.blur(input);
  expect(input).toHaveValue(15);
});
