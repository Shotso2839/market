import { toNano, Address } from '@ton/core';
import { PredictionMarket } from '../wrappers/PredictionMarket';
import { compile, NetworkProvider } from '@ton/blueprint';

export async function run(provider: NetworkProvider) {
    const code = await compile('PredictionMarket');

    // Параметры из переменных окружения (устанавливает contract_deployer.py)
    const betClosesAt     = parseInt(process.env.BET_CLOSES_AT     || '0');
    const platformAddress = process.env.PLATFORM_ADDRESS            || '';
    const feeBps          = parseInt(process.env.FEE_BPS           || '200');
    const marketId        = process.env.MARKET_ID                   || 'unknown';

    if (!betClosesAt || !platformAddress) {
        throw new Error('BET_CLOSES_AT and PLATFORM_ADDRESS env vars are required');
    }

    const market = PredictionMarket.createFromConfig(
        {
            creatorAddress:  provider.sender().address!,
            platformAddress: Address.parse(platformAddress),
            betClosesAt,
            feeBps,
        },
        code,
    );

    await market.sendDeploy(provider.sender(), toNano('0.05'));
    await provider.waitForDeploy(market.address);

    // Вывод в формате, который парсит contract_deployer.py
    console.log(`Market ID: ${marketId}`);
    console.log(`Адрес:     ${market.address.toString()}`);
    console.log(`Network:   ${provider.network()}`);
}
