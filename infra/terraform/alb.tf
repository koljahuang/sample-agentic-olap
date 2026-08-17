// Public entry point. HTTPS only: the certificate is required, and port 80
// exists solely to redirect. No listener ever serves content over plaintext.

resource "aws_lb" "service" {
  name               = "${local.prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "service" {
  name        = "${local.prefix}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.service.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.service.arn
  }
}

// Port 80 answers with a redirect and nothing else. Without it, anyone typing
// the hostname without a scheme gets a connection error rather than the app.
// Set enable_http_redirect = false to close port 80 entirely.
resource "aws_lb_listener" "http_redirect" {
  count             = var.enable_http_redirect ? 1 : 0
  load_balancer_arn = aws_lb.service.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

output "alb_dns_name" {
  value       = aws_lb.service.dns_name
  description = "Use the app_domain record instead; the certificate does not cover this name."
}

output "app_url" {
  value = "https://${var.app_domain}"
}
