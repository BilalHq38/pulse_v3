const path = require('path');

/** @type { import('@storybook/react-webpack5').StorybookConfig } */
module.exports = {
  stories: [
    '../src/**/*.mdx',
    '../src/**/*.stories.@(js|jsx|mjs|ts|tsx)',
  ],
  addons: [
    '@storybook/preset-create-react-app',
    '@storybook/addon-a11y',
    '@storybook/addon-docs',
    '@storybook/addon-onboarding',
  ],
  framework: '@storybook/react-webpack5',
  staticDirs: ['../public'],
  webpackFinal: async (baseConfig) => {
    baseConfig.resolve = baseConfig.resolve || {};
    baseConfig.resolve.alias = {
      ...(baseConfig.resolve.alias || {}),
      '@': path.resolve(__dirname, '../src'),
    };
    return baseConfig;
  },
};