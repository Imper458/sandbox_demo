from pypdf import PdfReader

# Read the PDF file
reader = PdfReader('/Convolutional neural network (CNN) and federated.pdf')

# Extract text from all pages
text = ""
for page in reader.pages:
    page_text = page.extract_text()
    if page_text:
        text += page_text + "\n"

# Print the extracted text (first 5000 characters to avoid too much output)
print(text[:5000])