from PIL import Image, ImageDraw, ImageFont
import random

def create_invoice(filename="test_invoice.png"):
    # Create white canvas
    width, height = 800, 1000
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)

    # Colors
    primary_color = (0, 71, 171) # Cobalt Blue
    text_color = (0, 0, 0)
    gray_color = (100, 100, 100)

    # Fonts (using default since system fonts are tricky)
    # Ideally we'd use nice TTF fonts, but default bitmap is safer for quick test
    
    # Header
    draw.rectangle([0, 0, width, 150], fill=primary_color)
    draw.text((50, 50), "ACME CORP", fill='white', font_size=40) # Might need PIL update for font_size
    # Fallback for old PIL if font_size param doesn't work directly on text()
    # But let's assume recent Pillow. If not, text will be tiny.

    draw.text((600, 50), "INVOICE", fill='white')
    draw.text((600, 70), "# INV-2024-001", fill='white')
    
    # Body
    y = 200
    left = 50
    right = 600
    
    # From
    draw.text((left, y), "FROM:", fill=gray_color)
    draw.text((left, y+20), "Acme Corporation", fill=text_color)
    draw.text((left, y+40), "123 Tech Lane", fill=text_color)
    draw.text((left, y+60), "San Francisco, CA 94107", fill=text_color)
    draw.text((left, y+80), "billing@acme.com", fill=text_color)
    
    # To
    draw.text((right, y), "BILL TO:", fill=gray_color)
    draw.text((right, y+20), "Globex Corporation", fill=text_color)
    draw.text((right, y+40), "742 Evergreen Terrace", fill=text_color)
    draw.text((right, y+60), "Springfield, IL 62704", fill=text_color)
    
    y += 150
    
    # Details
    draw.text((left, y), f"Date: 2024-02-12", fill=text_color)
    draw.text((right, y), f"Due Date: 2024-03-12", fill=text_color)
    
    y += 60
    
    # Table Header
    draw.rectangle([left, y, width-50, y+30], fill=(220, 220, 220))
    draw.text((left+10, y+8), "DESCRIPTION", fill=text_color)
    draw.text((400, y+8), "QTY", fill=text_color)
    draw.text((550, y+8), "PRICE", fill=text_color)
    draw.text((680, y+8), "TOTAL", fill=text_color)
    
    y += 40
    
    # Items
    items = [
        ("Web Development Services", "40", "150.00", "6,000.00"),
        ("Server Hosting (Annual)", "1", "1,200.00", "1,200.00"),
    ]
    
    for desc, qty, price, total in items:
        draw.text((left+10, y), desc, fill=text_color)
        draw.text((400, y), qty, fill=text_color)
        draw.text((550, y), f"${price}", fill=text_color)
        draw.text((680, y), f"${total}", fill=text_color)
        y += 30
    
    # Totals
    y += 50
    draw.line([400, y, width-50, y], fill=text_color, width=2)
    y += 20
    
    draw.text((550, y), "Subtotal:", fill=text_color)
    draw.text((680, y), "$7,200.00", fill=text_color)
    y += 30
    draw.text((550, y), "Tax (10%):", fill=text_color)
    draw.text((680, y), "$720.00", fill=text_color)
    y += 30
    
    # Grand Total
    draw.rectangle([540, y, width-50, y+40], fill=primary_color)
    draw.text((550, y+10), "TOTAL:", fill='white')
    draw.text((680, y+10), "$7,920.00", fill='white')
    
    # Footer
    draw.text((left, height-50), "Payment Terms: Net 30. Thank you for your business!", fill=gray_color)

    image.save(filename)
    print(f"Created {filename}")

if __name__ == "__main__":
    create_invoice()
