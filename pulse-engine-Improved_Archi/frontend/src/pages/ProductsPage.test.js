const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'ProductsPage.js'), 'utf8');
const apiSource = fs.readFileSync(path.join(__dirname, '..', 'lib', 'api.js'), 'utf8');
const bulkModalSource = fs.readFileSync(path.join(__dirname, '..', 'components', 'BulkUploadModal.js'), 'utf8');

test('Product bulk upload sends the spreadsheet file to the backend for image extraction', () => {
  expect(source).toContain("formData.append('file', file)");
  expect(source).toContain("api.post('/products/bulk-upload', formData");
  expect(source).not.toContain("headers: { 'Content-Type': 'multipart/form-data' }");
  expect(apiSource).toContain('isFormDataPayload(config.data)');
  expect(apiSource).toContain('removeContentTypeHeader(config.headers)');
});

test('Product upload UI supports common image formats including GIF', () => {
  expect(source).toContain("'image/gif'");
  expect(source).toContain('image_url');
});

test('Product bulk upload copy explains file-based image import', () => {
  expect(source).toContain('embedded XLSX images are imported with the original file');
  expect(bulkModalSource).toContain('requirementsText');
});
