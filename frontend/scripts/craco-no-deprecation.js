process.noDeprecation = true;
process.env.NODE_OPTIONS = `${process.env.NODE_OPTIONS || ''} --no-deprecation`.trim();

require('../node_modules/@craco/craco/dist/bin/craco');
