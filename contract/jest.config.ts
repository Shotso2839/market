import type { Config } from 'jest';

const config: Config = {
    preset: 'ts-jest',
    testEnvironment: 'node',
    testPathPattern: '/tests/',
    globalSetup: '@ton/blueprint/dist/jest-setup.js',
};

export default config;
