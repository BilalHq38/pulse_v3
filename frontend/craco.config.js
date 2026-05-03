const path = require("path");

const webpackConfig = {
  webpack: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
    configure: (config) => {
      config.watchOptions = {
        ...config.watchOptions,
        ignored: [
          "**/node_modules/**",
          "**/.git/**",
          "**/build/**",
          "**/dist/**",
          "**/coverage/**",
          "**/public/**",
        ],
      };
      return config;
    },
  },
  jest: {
    configure: {
      moduleNameMapper: {
        "^@/(.*)$": "<rootDir>/src/$1",
        "^react-router-dom$": "<rootDir>/node_modules/react-router-dom/dist/index.js",
        "^react-router$": "<rootDir>/node_modules/react-router/dist/development/index.js",
        "^react-router/dom$": "<rootDir>/node_modules/react-router/dist/development/dom-export.js",
      },
    },
  },
  devServer: (devServerConfig) => {
    const apiTarget = process.env.REACT_APP_BACKEND_URL || "http://127.0.0.1:8001";
    return {
      ...devServerConfig,
      proxy: {
        ...(devServerConfig.proxy || {}),
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    };
  },
};

module.exports = webpackConfig;
