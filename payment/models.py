from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
import uuid
from decimal import Decimal
from accounts.models import User
from registration.models import Contract
from dormitory.models import Room


class FeeType(models.Model):
    """Mô hình loại phí"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_('tên loại phí'), max_length=100)
    code = models.CharField(_('mã loại phí'), max_length=20, unique=True)
    description = models.TextField(_('mô tả'), blank=True, null=True)
    is_recurring = models.BooleanField(_('tính định kỳ'), default=True)
    is_active = models.BooleanField(_('đang hoạt động'), default=True)
    created_at = models.DateTimeField(_('thời gian tạo'), auto_now_add=True)
    updated_at = models.DateTimeField(_('thời gian cập nhật'), auto_now=True)

    class Meta:
        verbose_name = _('loại phí')
        verbose_name_plural = _('loại phí')
        ordering = ['name']

    def __str__(self):
        return self.name


class Invoice(models.Model):
    """Mô hình hóa đơn"""

    STATUS_CHOICES = (
        ('draft', 'Bản nháp'),
        ('pending', 'Chờ thanh toán'),
        ('partially_paid', 'Thanh toán một phần'),
        ('paid', 'Đã thanh toán'),
        ('overdue', 'Quá hạn'),
        ('canceled', 'Đã hủy'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(_('số hóa đơn'), max_length=50, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices', verbose_name=_('sinh viên'))
    contract = models.ForeignKey(Contract, on_delete=models.CASCADE, related_name='invoices',
                                 verbose_name=_('hợp đồng'), null=True, blank=True)
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, related_name='invoices', verbose_name=_('phòng'),
                             null=True, blank=True)
    issue_date = models.DateField(_('ngày phát hành'), default=timezone.now)
    due_date = models.DateField(_('ngày hết hạn'))
    total_amount = models.DecimalField(_('tổng tiền'), max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(_('số tiền đã thanh toán'), max_digits=10, decimal_places=2, default=0)
    status = models.CharField(_('trạng thái'), max_length=20, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField(_('ghi chú'), blank=True, null=True)
    month = models.PositiveSmallIntegerField(_('tháng'), null=True, blank=True)
    year = models.PositiveSmallIntegerField(_('năm'), null=True, blank=True)
    created_at = models.DateTimeField(_('thời gian tạo'), auto_now_add=True)
    updated_at = models.DateTimeField(_('thời gian cập nhật'), auto_now=True)

    class Meta:
        verbose_name = _('hóa đơn')
        verbose_name_plural = _('hóa đơn')
        ordering = ['-issue_date']

    def __str__(self):
        return f"{self.invoice_number} - {self.user.get_full_name()} - {self.total_amount} VNĐ"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            year = timezone.now().strftime("%Y")
            month = timezone.now().strftime("%m")
            count = Invoice.objects.filter(created_at__year=timezone.now().year).count() + 1
            self.invoice_number = f"HD-{year}{month}-{count:04d}"

        if self.total_amount <= 0:
            self.status = 'paid'
        elif self.paid_amount >= self.total_amount:
            self.status = 'paid'
        elif self.paid_amount > 0:
            self.status = 'partially_paid'
        elif timezone.now().date() > self.due_date:
            self.status = 'overdue'
        else:
            self.status = 'pending'

        super().save(*args, **kwargs)

    def get_remaining_amount(self):
        return max(0, self.total_amount - self.paid_amount)

    def record_payment(self, amount, payment_method='vnpay', transaction_id=None):
        """Ghi nhận thanh toán cho hóa đơn"""
        amount_decimal = Decimal(str(amount))
        payment = Payment.objects.create(
            invoice=self,
            user=self.user,
            amount=amount_decimal,
            payment_method=payment_method,
            transaction_id=transaction_id
        )
        self.paid_amount += amount_decimal
        self.save()
        return payment


class InvoiceItem(models.Model):
    """Mô hình mục trong hóa đơn"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items', verbose_name=_('hóa đơn'))
    fee_type = models.ForeignKey(FeeType, on_delete=models.CASCADE, related_name='invoice_items',
                                 verbose_name=_('loại phí'))
    description = models.CharField(_('mô tả'), max_length=255)
    quantity = models.PositiveIntegerField(_('số lượng'), default=1)
    unit_price = models.DecimalField(_('đơn giá'), max_digits=10, decimal_places=2)
    amount = models.DecimalField(_('thành tiền'), max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = _('mục hóa đơn')
        verbose_name_plural = _('mục hóa đơn')

    def __str__(self):
        return f"{self.description} - {self.amount} VNĐ"

    def save(self, *args, **kwargs):
        self.amount = self.quantity * self.unit_price

        super().save(*args, **kwargs)

        self.invoice.total_amount = sum(item.amount for item in self.invoice.items.all())
        self.invoice.save()


class Payment(models.Model):
    """Mô hình thanh toán"""

    PAYMENT_METHOD_CHOICES = (
        ('cash', 'Tiền mặt'),
        ('bank_transfer', 'Chuyển khoản'),
        ('vnpay', 'VNPay'),
        ('momo', 'MoMo'),
        ('zalopay', 'ZaloPay'),
        ('credit_card', 'Thẻ tín dụng'),
        ('other', 'Khác'),
    )

    STATUS_CHOICES = (
        ('pending', 'Đang xử lý'),
        ('completed', 'Hoàn thành'),
        ('failed', 'Thất bại'),
        ('refunded', 'Đã hoàn tiền'),
        ('canceled', 'Đã hủy'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='payments', verbose_name=_('hóa đơn'))
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments', verbose_name=_('người dùng'))
    amount = models.DecimalField(_('số tiền'), max_digits=10, decimal_places=2)
    payment_date = models.DateTimeField(_('ngày thanh toán'), default=timezone.now)
    payment_method = models.CharField(_('phương thức thanh toán'), max_length=20, choices=PAYMENT_METHOD_CHOICES,
                                      default='vnpay')
    transaction_id = models.CharField(_('mã giao dịch'), max_length=100, blank=True, null=True)
    status = models.CharField(_('trạng thái'), max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(_('ghi chú'), blank=True, null=True)
    created_at = models.DateTimeField(_('thời gian tạo'), auto_now_add=True)
    updated_at = models.DateTimeField(_('thời gian cập nhật'), auto_now=True)

    class Meta:
        verbose_name = _('thanh toán')
        verbose_name_plural = _('thanh toán')
        ordering = ['-payment_date']

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.amount} VNĐ - {self.get_payment_method_display()}"


class ElectricityReading(models.Model):
    """Mô hình chỉ số điện"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='electricity_readings',
                             verbose_name=_('phòng'))
    month = models.PositiveSmallIntegerField(_('tháng'))
    year = models.PositiveSmallIntegerField(_('năm'))
    reading_date = models.DateField(_('ngày ghi'))
    previous_reading = models.PositiveIntegerField(_('chỉ số cũ'))
    current_reading = models.PositiveIntegerField(_('chỉ số mới'))
    unit_price = models.DecimalField(_('đơn giá'), max_digits=10, decimal_places=2)
    amount = models.DecimalField(_('thành tiền'), max_digits=10, decimal_places=2, default=0)
    is_billed = models.BooleanField(_('đã lập hóa đơn'), default=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, related_name='electricity_readings',
                                verbose_name=_('hóa đơn'), null=True, blank=True)
    notes = models.TextField(_('ghi chú'), blank=True, null=True)
    created_at = models.DateTimeField(_('thời gian tạo'), auto_now_add=True)
    updated_at = models.DateTimeField(_('thời gian cập nhật'), auto_now=True)

    class Meta:
        verbose_name = _('chỉ số điện')
        verbose_name_plural = _('chỉ số điện')
        ordering = ['-year', '-month']
        unique_together = ('room', 'month', 'year')

    def __str__(self):
        return f"{self.room} - {self.month}/{self.year} - {self.get_usage()} kWh"

    def get_usage(self):
        return max(0, self.current_reading - self.previous_reading)

    def save(self, *args, **kwargs):
        usage = self.get_usage()
        self.amount = usage * self.unit_price

        super().save(*args, **kwargs)


class WaterReading(models.Model):
    """Mô hình chỉ số nước"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='water_readings', verbose_name=_('phòng'))
    month = models.PositiveSmallIntegerField(_('tháng'))
    year = models.PositiveSmallIntegerField(_('năm'))
    reading_date = models.DateField(_('ngày ghi'))
    previous_reading = models.PositiveIntegerField(_('chỉ số cũ'))
    current_reading = models.PositiveIntegerField(_('chỉ số mới'))
    unit_price = models.DecimalField(_('đơn giá'), max_digits=10, decimal_places=2)
    amount = models.DecimalField(_('thành tiền'), max_digits=10, decimal_places=2, default=0)
    is_billed = models.BooleanField(_('đã lập hóa đơn'), default=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, related_name='water_readings',
                                verbose_name=_('hóa đơn'), null=True, blank=True)
    notes = models.TextField(_('ghi chú'), blank=True, null=True)
    created_at = models.DateTimeField(_('thời gian tạo'), auto_now_add=True)
    updated_at = models.DateTimeField(_('thời gian cập nhật'), auto_now=True)

    class Meta:
        verbose_name = _('chỉ số nước')
        verbose_name_plural = _('chỉ số nước')
        ordering = ['-year', '-month']
        unique_together = ('room', 'month', 'year')

    def __str__(self):
        return f"{self.room} - {self.month}/{self.year} - {self.get_usage()} m³"

    def get_usage(self):
        return max(0, self.current_reading - self.previous_reading)

    def save(self, *args, **kwargs):
        usage = self.get_usage()
        self.amount = usage * self.unit_price

        super().save(*args, **kwargs)


class VNPayTransaction(models.Model):
    """Mô hình giao dịch VNPay"""

    STATUS_CHOICES = (
        ('pending', 'Đang xử lý'),
        ('success', 'Thành công'),
        ('failed', 'Thất bại'),
        ('canceled', 'Đã hủy'),
        ('refunded', 'Đã hoàn tiền'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='vnpay_transactions',
                             verbose_name=_('người dùng'))
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name='vnpay_transaction',
                                   verbose_name=_('thanh toán'), null=True, blank=True)
    amount = models.DecimalField(_('số tiền'), max_digits=10, decimal_places=2)
    order_info = models.CharField(_('thông tin đơn hàng'), max_length=255)
    transaction_no = models.CharField(_('mã giao dịch VNPay'), max_length=100, blank=True, null=True)
    transaction_status = models.CharField(_('trạng thái giao dịch'), max_length=20, choices=STATUS_CHOICES,
                                          default='pending')
    bank_code = models.CharField(_('mã ngân hàng'), max_length=20, blank=True, null=True)
    card_type = models.CharField(_('loại thẻ'), max_length=20, blank=True, null=True)
    txn_ref = models.CharField(_('mã tham chiếu'), max_length=100, unique=True)
    secure_hash = models.CharField(_('mã băm bảo mật'), max_length=255, blank=True, null=True)
    transaction_date = models.DateTimeField(_('thời gian giao dịch'), blank=True, null=True)
    response_code = models.CharField(_('mã phản hồi'), max_length=10, blank=True, null=True)
    response_message = models.CharField(_('thông báo phản hồi'), max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(_('thời gian tạo'), auto_now_add=True)
    updated_at = models.DateTimeField(_('thời gian cập nhật'), auto_now=True)

    class Meta:
        verbose_name = _('giao dịch VNPay')
        verbose_name_plural = _('giao dịch VNPay')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.amount} VNĐ - {self.get_transaction_status_display()}"