import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { PushNotificationsCard } from './PushNotificationsCard';
import { getPushPublicKey, subscribePush, unsubscribePush } from '../../api';
import { saveFavoriteTeams } from '../../layout/userSettings';
import { writeCenterContext } from '../../layout/centerContext';
vi.mock('../../api', () => ({getPushPublicKey:vi.fn(),subscribePush:vi.fn(async () => ({})),unsubscribePush:vi.fn(),sendPushTest:vi.fn()}));
vi.mock('../../platform/runtime', () => ({isNativeApp: () => false}));
afterEach(() => {cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks();});
function support(subscribed = false) {
  vi.stubGlobal('PushManager', class {});
  vi.stubGlobal('Notification', {permission:'granted'});
  vi.stubGlobal('navigator', {serviceWorker:{ready:Promise.resolve({pushManager:{getSubscription:async () => subscribed ? {endpoint:'https://push.example/device',toJSON: () => ({keys:{p256dh:'test',auth:'test'}}),unsubscribe: async () => true} : null}})}});
}
it('distinguishes unreachable configuration from a server with alerts disabled and retries', async () => {
  support();
  vi.mocked(getPushPublicKey).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({configured:false,public_key:''} as never);
  render(<PushNotificationsCard />);
  await screen.findByText(/Couldn't reach the server/);
  expect(screen.queryByText(/aren't switched on/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('Retry'));
  await screen.findByText(/aren't switched on/);
  expect(getPushPublicKey).toHaveBeenCalledTimes(2);
});
it('updates and displays subscribed event and favorite teams', async () => {
  support(true);
  vi.mocked(getPushPublicKey).mockResolvedValue({configured:true,public_key:'abc'} as never);
  writeCenterContext({eventKey:'2026old'});
  saveFavoriteTeams(['frc1']);
  render(<PushNotificationsCard />);
  await screen.findByText('Alerts cover 2026old and #1.');
  await act(async () => {writeCenterContext({eventKey:'2026new'}); saveFavoriteTeams(['frc2']);});
  await screen.findByText('Alerts cover 2026new and #2.');
  await waitFor(() => expect(subscribePush).toHaveBeenLastCalledWith(expect.objectContaining({event_key:'2026new',team_keys:['frc2']})));
});
it('an update waiting in the queue cannot resubscribe after Disable', async () => {
  support(true);
  vi.mocked(getPushPublicKey).mockResolvedValue({configured:true,public_key:'abc'} as never);
  let releaseFirst: () => void = () => undefined;
  vi.mocked(subscribePush).mockImplementationOnce(() => new Promise((resolve) => { releaseFirst = () => resolve({} as never); }));
  writeCenterContext({eventKey:'2026old'});
  render(<PushNotificationsCard />);
  await waitFor(() => expect(subscribePush).toHaveBeenCalledTimes(1));
  // A second update queues behind the first, then the user turns alerts off.
  await act(async () => {writeCenterContext({eventKey:'2026new'});});
  fireEvent.click(await screen.findByText('Disable'));
  await act(async () => {releaseFirst(); await Promise.resolve();});
  await screen.findByText('Notifications disabled.');
  expect(unsubscribePush).toHaveBeenCalledTimes(1);
  expect(subscribePush).toHaveBeenCalledTimes(1);
});
it('an update still looking up its subscription when Disable starts never subscribes afterwards', async () => {
  vi.mocked(getPushPublicKey).mockResolvedValue({configured:true,public_key:'abc'} as never);
  vi.mocked(subscribePush).mockClear();
  vi.mocked(unsubscribePush).mockClear();
  const sub = {endpoint:'https://push.example/device',toJSON: () => ({keys:{p256dh:'test',auth:'test'}}),unsubscribe: async () => true};
  const pending: Array<() => void> = [];
  let holdLookups = false;
  vi.stubGlobal('PushManager', class {});
  vi.stubGlobal('Notification', {permission:'granted'});
  vi.stubGlobal('navigator', {serviceWorker:{ready:Promise.resolve({pushManager:{getSubscription:() => holdLookups
    ? new Promise((resolve) => { pending.push(() => resolve(sub)); })
    : Promise.resolve(sub)}})}});
  writeCenterContext({eventKey:'2026old'});
  render(<PushNotificationsCard />);
  await waitFor(() => expect(subscribePush).toHaveBeenCalledTimes(1));
  // A coverage update starts and is stuck looking up the subscription...
  holdLookups = true;
  await act(async () => {writeCenterContext({eventKey:'2026new'});});
  await waitFor(() => expect(pending.length).toBeGreaterThan(0));
  // ...and answers the moment the server confirms the unsubscribe, before the screen updates.
  holdLookups = false;
  vi.mocked(unsubscribePush).mockImplementationOnce(async () => { pending.splice(0).forEach((release) => release()); return {} as never; });
  fireEvent.click(await screen.findByText('Disable'));
  await screen.findByText('Notifications disabled.');
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
  expect(unsubscribePush).toHaveBeenCalledTimes(1);
  expect(subscribePush).toHaveBeenCalledTimes(1);
});
it('a preference change made while Disable is still running never re-registers', async () => {
  support(true);
  vi.mocked(getPushPublicKey).mockResolvedValue({configured:true,public_key:'abc'} as never);
  vi.mocked(subscribePush).mockClear();
  vi.mocked(unsubscribePush).mockClear();
  writeCenterContext({eventKey:'2026old'});
  render(<PushNotificationsCard />);
  await waitFor(() => expect(subscribePush).toHaveBeenCalledTimes(1));
  let finishUnsubscribe: () => void = () => undefined;
  vi.mocked(unsubscribePush).mockImplementationOnce(() => new Promise((resolve) => { finishUnsubscribe = () => resolve({} as never); }));
  fireEvent.click(await screen.findByText('Disable'));
  await waitFor(() => expect(unsubscribePush).toHaveBeenCalledTimes(1));
  // The user changes event while the server is still removing the subscription.
  await act(async () => {writeCenterContext({eventKey:'2026new'});});
  await act(async () => {finishUnsubscribe(); await new Promise((resolve) => setTimeout(resolve, 50));});
  await screen.findByText('Notifications disabled.');
  expect(subscribePush).toHaveBeenCalledTimes(1);
});
it('alerts can be turned back on after Disable', async () => {
  support(true);
  vi.stubGlobal('Notification', {permission:'granted', requestPermission: async () => 'granted'});
  vi.mocked(getPushPublicKey).mockResolvedValue({configured:true,public_key:'abc'} as never);
  vi.mocked(subscribePush).mockClear();
  render(<PushNotificationsCard />);
  await waitFor(() => expect(subscribePush).toHaveBeenCalledTimes(1));
  fireEvent.click(await screen.findByText('Disable'));
  await screen.findByText('Notifications disabled.');
  fireEvent.click(await screen.findByText('Enable match alerts'));
  await screen.findByText('Match alerts enabled for this device.');
  // Re-enabling isn't blocked: the enable itself re-registers (the coverage update may follow).
  expect(vi.mocked(subscribePush).mock.calls.length).toBeGreaterThanOrEqual(2);
});
