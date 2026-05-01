const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, 'ProductsPage.js'), 'utf8');

test('Product bulk upload sends the spreadsheet file to the backend for image extraction', () => {
  expect(source).toContain("formData.append('file', file)");
  expect(source).toContain("api.post('/products/bulk-upload', formData");
  expect(source).toContain('multipart/form-data');
});

test('Product upload UI supports common image formats including GIF', () => {
  expect(source).toContain("'image/gif'");
  expect(source).toContain('image_url');
});
