import {
    Address, beginCell, Cell, Contract, contractAddress,
    ContractProvider, Sender, SendMode, toNano, TupleReader,
} from '@ton/core';

export type PredictionMarketConfig = {
    creatorAddress: Address;
    platformAddress: Address;
    betClosesAt: number;
    feeBps: number;
};

export type MarketData = {
    status: number;
    yesPool: bigint;
    noPool: bigint;
    betClosesAt: number;
    winningOutcome: number;
};

export const Opcodes = {
    PLACE_BET: 0x01,
    RESOLVE:   0x02,
    CANCEL:    0x03,
    CLAIM:     0x04,
} as const;

export class PredictionMarket implements Contract {
    constructor(readonly address: Address, readonly init?: { code: Cell; data: Cell }) {}

    static createFromConfig(config: PredictionMarketConfig, code: Cell, workchain = 0): PredictionMarket {
        const data = PredictionMarket.buildInitData(config);
        const init = { code, data };
        return new PredictionMarket(contractAddress(workchain, init), init);
    }

    static buildInitData(config: PredictionMarketConfig): Cell {
        return beginCell()
            .storeAddress(config.creatorAddress)
            .storeUint(0, 8)
            .storeCoins(0)
            .storeCoins(0)
            .storeUint(config.betClosesAt, 32)
            .storeUint(0, 8)
            .storeAddress(config.platformAddress)
            .storeUint(config.feeBps, 16)
            .endCell();
    }

    async sendDeploy(provider: ContractProvider, via: Sender, value: bigint) {
        await provider.internal(via, {
            value, sendMode: SendMode.PAY_GAS_SEPARATELY,
            body: beginCell().endCell(),
        });
    }

    async sendPlaceBet(provider: ContractProvider, via: Sender,
                       opts: { outcome: 1 | 2; amountTon: number }) {
        await provider.internal(via, {
            value: toNano(opts.amountTon.toString()),
            sendMode: SendMode.PAY_GAS_SEPARATELY,
            body: beginCell().storeUint(Opcodes.PLACE_BET, 32).storeUint(opts.outcome, 8).endCell(),
        });
    }

    async sendResolve(provider: ContractProvider, via: Sender,
                      opts: { outcome: 1 | 2; value?: bigint }) {
        await provider.internal(via, {
            value: opts.value ?? toNano('0.05'),
            sendMode: SendMode.PAY_GAS_SEPARATELY,
            body: beginCell().storeUint(Opcodes.RESOLVE, 32).storeUint(opts.outcome, 8).endCell(),
        });
    }

    async sendCancel(provider: ContractProvider, via: Sender, value: bigint = toNano('0.05')) {
        await provider.internal(via, {
            value, sendMode: SendMode.PAY_GAS_SEPARATELY,
            body: beginCell().storeUint(Opcodes.CANCEL, 32).endCell(),
        });
    }

    async sendClaim(provider: ContractProvider, via: Sender, value: bigint = toNano('0.05')) {
        await provider.internal(via, {
            value, sendMode: SendMode.PAY_GAS_SEPARATELY,
            body: beginCell().storeUint(Opcodes.CLAIM, 32).endCell(),
        });
    }

    async getMarketData(provider: ContractProvider): Promise<MarketData> {
        const result = await provider.get('get_market_data', []);
        const stack: TupleReader = result.stack;
        return {
            status:         stack.readNumber(),
            yesPool:        stack.readBigNumber(),
            noPool:         stack.readBigNumber(),
            betClosesAt:    stack.readNumber(),
            winningOutcome: stack.readNumber(),
        };
    }

    async getBet(provider: ContractProvider, address: Address): Promise<{ outcome: number; amount: bigint }> {
        const result = await provider.get('get_bet', [
            { type: 'slice', cell: beginCell().storeAddress(address).endCell() },
        ]);
        return { outcome: result.stack.readNumber(), amount: result.stack.readBigNumber() };
    }
}
