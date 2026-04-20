import { Blockchain, SandboxContract, TreasuryContract } from '@ton/sandbox';
import { toNano } from '@ton/core';
import { PredictionMarket } from '../wrappers/PredictionMarket';
import { compile } from '@ton/blueprint';
import '@ton/test-utils';

describe('PredictionMarket', () => {
    let code: import('@ton/core').Cell;
    let blockchain: Blockchain;
    let creator: SandboxContract<TreasuryContract>;
    let platform: SandboxContract<TreasuryContract>;
    let alice: SandboxContract<TreasuryContract>;
    let bob: SandboxContract<TreasuryContract>;
    let market: SandboxContract<PredictionMarket>;

    const FUTURE = Math.floor(Date.now() / 1000) + 86400;
    const PAST   = Math.floor(Date.now() / 1000) - 3600;

    beforeAll(async () => { code = await compile('PredictionMarket'); });

    beforeEach(async () => {
        blockchain = await Blockchain.create();
        creator  = await blockchain.treasury('creator');
        platform = await blockchain.treasury('platform');
        alice    = await blockchain.treasury('alice');
        bob      = await blockchain.treasury('bob');

        market = blockchain.openContract(
            PredictionMarket.createFromConfig(
                { creatorAddress: creator.address, platformAddress: platform.address,
                  betClosesAt: FUTURE, feeBps: 200 },
                code,
            ),
        );

        const deploy = await market.sendDeploy(creator.getSender(), toNano('0.05'));
        expect(deploy.transactions).toHaveTransaction({ deploy: true, success: true });
    });

    // ── Deploy ─────────────────────────────────────────────────────────────────

    it('deploys with status=open and empty pools', async () => {
        const d = await market.getMarketData();
        expect(d.status).toBe(0);
        expect(d.yesPool).toBe(0n);
        expect(d.noPool).toBe(0n);
        expect(d.winningOutcome).toBe(0);
    });

    // ── place_bet ──────────────────────────────────────────────────────────────

    it('accepts YES bet and updates yes_pool', async () => {
        await market.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 5 });
        const d = await market.getMarketData();
        expect(d.yesPool).toBeGreaterThan(0n);
        expect(d.noPool).toBe(0n);
    });

    it('accepts NO bet and updates no_pool', async () => {
        await market.sendPlaceBet(bob.getSender(), { outcome: 2, amountTon: 3 });
        const d = await market.getMarketData();
        expect(d.noPool).toBeGreaterThan(0n);
    });

    it('rejects bet below 0.1 TON (exit 102)', async () => {
        const r = await market.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 0.05 });
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 102 });
    });

    it('rejects bet after deadline (exit 101)', async () => {
        const pm = blockchain.openContract(
            PredictionMarket.createFromConfig(
                { creatorAddress: creator.address, platformAddress: platform.address,
                  betClosesAt: PAST, feeBps: 200 }, code));
        await pm.sendDeploy(creator.getSender(), toNano('0.05'));
        const r = await pm.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 5 });
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 101 });
    });

    it('rejects betting both sides from same address (exit 104)', async () => {
        await market.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 5 });
        const r = await market.sendPlaceBet(alice.getSender(), { outcome: 2, amountTon: 3 });
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 104 });
    });

    // ── resolve ────────────────────────────────────────────────────────────────

    it('creator resolves after deadline → status=resolved', async () => {
        const pm = blockchain.openContract(
            PredictionMarket.createFromConfig(
                { creatorAddress: creator.address, platformAddress: platform.address,
                  betClosesAt: PAST, feeBps: 200 }, code));
        await pm.sendDeploy(creator.getSender(), toNano('0.1'));
        await pm.sendResolve(creator.getSender(), { outcome: 1 });
        const d = await pm.getMarketData();
        expect(d.status).toBe(2);
        expect(d.winningOutcome).toBe(1);
    });

    it('rejects resolve from non-creator (exit 200)', async () => {
        const r = await market.sendResolve(alice.getSender(), { outcome: 1 });
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 200 });
    });

    it('rejects resolve before deadline (exit 202)', async () => {
        const r = await market.sendResolve(creator.getSender(), { outcome: 1 });
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 202 });
    });

    // ── Full flow ──────────────────────────────────────────────────────────────

    it('full flow: alice YES wins, bob NO loses, fee to platform', async () => {
        const pm = blockchain.openContract(
            PredictionMarket.createFromConfig(
                { creatorAddress: creator.address, platformAddress: platform.address,
                  betClosesAt: PAST, feeBps: 200 }, code));
        await pm.sendDeploy(creator.getSender(), toNano('0.1'));

        blockchain.now = PAST - 100;
        await pm.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 10 });
        await pm.sendPlaceBet(bob.getSender(),   { outcome: 2, amountTon: 5  });
        blockchain.now = PAST + 100;

        const platformBefore = await platform.getBalance();
        await pm.sendResolve(creator.getSender(), { outcome: 1 });

        // Platform received fee
        const platformAfter = await platform.getBalance();
        expect(platformAfter - platformBefore).toBeGreaterThan(toNano('0.1'));

        // Alice claims ~14.7 TON (15 total × 98%)
        const aliceBefore = await alice.getBalance();
        await pm.sendClaim(alice.getSender());
        const aliceAfter = await alice.getBalance();
        expect(aliceAfter - aliceBefore).toBeGreaterThan(toNano('13'));

        // Bob can't claim (loser, exit 403)
        const r = await pm.sendClaim(bob.getSender());
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 403 });
    });

    it('prevents double-claim (exit 401)', async () => {
        const pm = blockchain.openContract(
            PredictionMarket.createFromConfig(
                { creatorAddress: creator.address, platformAddress: platform.address,
                  betClosesAt: PAST, feeBps: 200 }, code));
        await pm.sendDeploy(creator.getSender(), toNano('0.1'));
        blockchain.now = PAST - 100;
        await pm.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 5 });
        blockchain.now = PAST + 100;
        await pm.sendResolve(creator.getSender(), { outcome: 1 });
        await pm.sendClaim(alice.getSender());
        const r = await pm.sendClaim(alice.getSender());
        expect(r.transactions).toHaveTransaction({ success: false, exitCode: 401 });
    });

    // ── cancel + refund ────────────────────────────────────────────────────────

    it('cancel → status=cancelled, bettor gets refund', async () => {
        await market.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 7 });
        await market.sendCancel(creator.getSender());
        const d = await market.getMarketData();
        expect(d.status).toBe(3);

        const aliceBefore = await alice.getBalance();
        await market.sendClaim(alice.getSender());
        const aliceAfter = await alice.getBalance();
        expect(aliceAfter - aliceBefore).toBeGreaterThan(toNano('6.5'));
    });

    // ── Getters ────────────────────────────────────────────────────────────────

    it('get_bet returns outcome and amount after bet', async () => {
        await market.sendPlaceBet(alice.getSender(), { outcome: 1, amountTon: 3 });
        const bet = await market.getBet(alice.address);
        expect(bet.outcome).toBe(1);
        expect(bet.amount).toBeGreaterThan(toNano('2.9'));
    });

    it('get_bet returns zeros for unknown address', async () => {
        const bet = await market.getBet(bob.address);
        expect(bet.outcome).toBe(0);
        expect(bet.amount).toBe(0n);
    });
});
