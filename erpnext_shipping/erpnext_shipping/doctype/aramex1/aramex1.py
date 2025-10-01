import datetime
import json
import re
import time
import xml.etree.ElementTree as ET
from json import dumps as json_dumps

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import get_link_to_form
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

ARAMEX_PROVIDER = "Aramex1"
PROD_BASE_URL = "https://ws.aramex1.net/shippingapi"
TEST_BASE_URL = "https://ws.sbx.aramex1.net/ShippingAPI.V2/"


class Aramex1(Document):
	def validate(self):
		# if not self.enable:
		# 	return

		# utils = Aramex1Utils()
		# try:
		# 	utils.request("POST", "GetServices")
		# except HTTPError as e:
		# 	if e.response.status_code == 401:
		# 		frappe.throw(_("Invalid API Credentials"))
		# 	elif e.response.status_code == 400 and self.is_sandbox:
		# 		frappe.msgprint(_("Sandbox environment: skipping GetServices test (HTTP 400)"))
		# 	else:
		# 		frappe.throw(
		# 			_("There was an error with the Aramex1 API. HTTP Status Code: {0}").format(
		# 				e.response.status_code
		# 			)
		# 		)
		pass


class Aramex1Utils:
	def __init__(self):
		settings = frappe.get_single("Aramex1")
		self.base_url = TEST_BASE_URL if settings.is_sandbox else PROD_BASE_URL
		self.username = settings.user_name
		self.password = settings.get_password("password")
		self.account_number = settings.account_number
		self.account_pin = settings.account_pin
		self.account_entity = settings.account_entity
		self.account_country_code = settings.account_country_code

	################################ get service & get rate details #################################

	def get_available_services(self, delivery_address, pickup_address, parcels: list[dict]):
		# Guard for credentials
		if not self.username or not self.password or not self.account_number:
			frappe.log_error("Missing Aramex1 credentials", "Aramex1 Service Error")
			return []

		# Convert country codes to uppercase
		to_country = delivery_address.country_code.upper()
		from_country = pickup_address.country_code.upper()

		# Build payload
		payload = {
			"ClientInfo": self.get_client_info(),
			"Transaction": self.get_transaction_reference(),
			"OriginAddress": self.get_address_details(pickup_address),
			"DestinationAddress": self.get_address_details(delivery_address),
			"ShipmentDetails": self.get_shipment_details(parcels, from_country, to_country),
		}

		url = f"{self.base_url}/RateCalculator/Service_1_0.svc/json/CalculateRate"
		headers = {"Content-Type": "application/json", "Accept": "application/json"}

		try:
			response = requests.post(url, json=payload, headers=headers, timeout=30)
			# Handle non-JSON responses
			try:
				response_data = response.json()
			except ValueError:
				frappe.log_error(
					f"Non-JSON response from Aramex1 ({response.status_code}): {response.text}",
					"Aramex1 Response Error",
				)
				frappe.throw(_("Failed to fetch Aramex1 services: Invalid response"))
				return []

			# Check for API-level errors
			if response_data.get("HasErrors"):
				notifications = response_data.get("Notifications", [])
				if notifications:
					error_messages = "\n".join(
						[f"{n.get('Code')}: {n.get('Message')}" for n in notifications]
					)
					frappe.throw(_("Aramex1 returned errors:\n{0}").format(error_messages))

			# Extract services
			services = response_data.get("Services") or []
			total_amount = response_data.get("TotalAmount")

			# If no detailed services, fallback to total amount
			if not services and total_amount:
				services = [
					{
						"Provider": ARAMEX_PROVIDER.upper(),
						"Name": ARAMEX_PROVIDER,
						"Carrier": ARAMEX_PROVIDER,
						"Amount": total_amount.get("Value"),
						"CurrencyCode": total_amount.get("CurrencyCode"),
					}
				]

			# Convert services to standardized dict
			available_services = [self.get_service_dict(service) for service in services]

			return available_services

		except requests.exceptions.RequestException as e:
			frappe.log_error(f"Network error: {str(e)}", "Aramex1 Service Error")
			frappe.throw(_("Network error occurred while fetching Aramex1 services"))
			return []

		except Exception as e:
			frappe.log_error(f"Unexpected error: {str(e)}", "Aramex1 Service Error")
			show_error_alert("fetching Aramex1 services")
			return []

	########################## create shipment ##################################

	def create_shipment(
		self,
		shipment,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		shipment_details,
		service_info,
	):
		"""Create Aramex1 shipment"""
		if isinstance(shipment_details, str):
			shipment_details = json.loads(shipment_details)

		dt = "2025-09-17T10:00:00"
		if isinstance(dt, str):
			dt = datetime.datetime.fromisoformat(dt)
		millis = int(time.mktime(dt.timetuple()) * 1000)
		pickup_date = f"/Date({millis})/"
		from_country = pickup_address.country_code.upper()
		to_country = delivery_address.country_code.upper()

		payload = {
			"ClientInfo": self.get_client_info(),
			"LabelInfo": {"ReportID": 9729, "ReportType": "URL"},
			"Shipments": [
				{
					"Shipper": {
						"Reference1": "shipper ref",
						"AccountNumber": self.account_number,
						"AccountPin": self.account_pin,
						"AccountEntity": self.account_entity,
						"AccountCountryCode": self.account_country_code,
						"PartyAddress": self.get_address_details(pickup_address),
						"Contact": self.get_contact_details(pickup_contact, "shipper"),
					},
					"Consignee": {
						"Reference1": "consignee ref",
						"PartyAddress": self.get_address_details(delivery_address),
						"Contact": self.get_contact_details(delivery_contact, "consignee"),
					},
					"ShippingDateTime": pickup_date,
					"Details": self.get_shipment_details(shipment, from_country, to_country),
				}
			],
		}

		# {
		# 				"NumberOfPieces": shipment_details[0].get("count", 1),
		# 				"ActualWeight": {
		# 					"Value": shipment_details[0].get("weight", 0),
		# 					"Unit": "KG"
		# 				},
		# 				"ProductGroup": "EXP",
		# 				"ProductType": "PDX",
		# 				"PaymentType": "P",
		# 				"PaymentOptions": "CASH",
		# 				"DescriptionOfGoods": "Sample goods",
		# 				"GoodsOriginCountry": pickup_address.get("country_code", "OM").upper(),
		# 				"CustomsValueamount": 0,
		# 				"CashonDelivery":0,
		# 				"CashAddtionalAmount":0,
		# 				"CollectedAmount":0,
		# 				"Dimensions": {
		# 					"Length": shipment_details[0].get("length", 0),
		# 					"Width": shipment_details[0].get("width", 0),
		# 					"Height": shipment_details[0].get("height", 0),
		# 					"Unit": "CM"
		# 				},
		# 				"ChargeableWeight": {
		# 					"Value": shipment_details[0].get("weight", 5),
		# 					"Unit": "KG"
		# 				},
		# 			}

		print("Create Shipment Payload:", json.dumps(payload, indent=4))

		try:
			url = f"{self.base_url.rstrip('/')}/Shipping/Service_1_0.svc/json/CreateShipments"
			headers = {"Content-Type": "application/json", "Accept": "application/json"}

			response = requests.post(url, json=payload, headers=headers)
			print("Response Status Code:", response.status_code)
			print("Response Text:", response.text)

			response_json = response.json()
			print("Response JSON:", response_json)
			print("Create Shipment Response:", json.dumps(response_json, indent=4))

			# Parse result
			if response_json.get("HasErrors"):
				error_msg = response_json.get("Notifications", [{}])[0].get("Message", "Unknown error")
				print("Aramex1 Error:", error_msg)
				return None

			shipments = response_json.get("Shipments")
			if shipments and len(shipments) > 0:
				shipment = shipments[0]
				return {
					"service_provider": "Aramex1",
					"shipment_id": shipment.get("ID"),
					"carrier": shipment.get("ProductGroup"),
					"carrier_service": shipment.get("ProductType"),
					"shipment_amount": shipment.get("TotalAmount") or 0,
					"awb_number": shipment.get("ShipmentNumber") or shipment.get("ID"),
					"label_url": shipment.get("ShipmentLabel", {}).get("LabelURL"),  # URL for the label
				}

		except Exception as e:
			print("Exception occurred:", e)
			show_error_alert("creating Aramex1 shipment")

	########################## get_label ##################################

	def get_label(self, shipment_number):
		try:
			payload = {
				"ClientInfo": {
					"UserName": self.username,
					"Password": self.password,
					"AccountNumber": self.account_number,
					"AccountPin": self.account_pin,
					"AccountEntity": self.account_entity,
					"AccountCountryCode": self.account_country_code,
					"Version": "v1",
				},
				"LabelInfo": {"ReportID": 9729, "ReportType": "URL"},
				"ShipmentNumber": shipment_number,
			}

			url = f"{self.base_url.rstrip('/')}/Shipping/Service_1_0.svc/json/PrintLabel"
			headers = {"Content-Type": "application/json", "Accept": "application/json"}
			response = requests.post(url, json=payload, headers=headers)
			response_json = response.json()
			label_url = response_json.get("ShipmentLabel", {}).get("LabelURL")
			return label_url

		except Exception:
			show_error_alert("fetching Aramex1 label")

	def get_tracking_data(self, shipment_number):
		"""Fetch tracking data for Aramex1 shipment"""
		try:
			tracking_data = self.request("GET", f"TrackShipments/{shipment_number}")
			if tracking_data.get("TrackingResults"):
				result = tracking_data["TrackingResults"][0]
				return {
					"awb_number": result["WaybillNumber"],
					"tracking_status": result["UpdateStatus"],
					"tracking_status_info": result["StatusDescription"],
					"tracking_url": f"https://www.aramex1.com/track?number={result['WaybillNumber']}",
				}
		except Exception:
			show_error_alert("updating Aramex1 shipment")

	def get_client_info(self):
		return {
			"UserName": self.username,
			"Password": self.password,
			"Version": "v1",
			"AccountNumber": self.account_number,
			"AccountPin": self.account_pin,
			"AccountEntity": self.account_entity,
			"AccountCountryCode": self.account_country_code,
		}

	def get_transaction_reference(self):
		return {
			"Reference1": "Ref1",
			"Reference2": "",
			"Reference3": "",
			"Reference4": "",
			"Reference5": "",
		}

	def get_address_details(self, address):
		country = address.country_code.upper()
		return {
			"Line1": address.address_line1,
			"Line2": address.address_line2 or "",
			"Line3": "",
			"City": address.city,
			"StateOrProvinceCode": address.state,
			"PostCode": address.pincode,
			"CountryCode": country,
		}

	def get_contact_details(self, contact, contact_type):
		print("--------------contact---------------------")

		company_name = ""
		if not company_name:
			if contact.get("email_id"):
				employee = frappe.db.get_value(
					"Employee", {"user_id": contact.get("email_id")}, ["company"], as_dict=True
				)
				if employee and employee.get("company"):
					company_name = employee["company"]

		if not company_name:
			company_name = frappe.db.get_single_value("Global Defaults", "default_company")

		if not company_name:
			frappe.throw(
				_(
					"No company could be found for this contact. Please set Company Name or configure Default Company."
				)
			)

		phone = contact.get("phone")
		if phone and not phone.startswith("+"):
			phone = "+" + phone.replace(" ", "").replace("-", "")

		mobile_no = contact.get("mobile_no")
		if mobile_no and not mobile_no.startswith("+"):
			mobile_no = "+" + mobile_no.replace(" ", "").replace("-", "")

		return {
			"PersonName": contact.get("first_name"),
			"CompanyName": company_name,
			"PhoneNumber1": phone,
			"PhoneNumber2": "",
			"CellPhone": mobile_no,
			"EmailAddress": contact.get("email_id"),
			"Type": contact_type,
		}

	def get_shipment_details(self, shipment, from_country, to_country):
		# Calculate max values from parcels
		total_pieces = 0
		total_actual_weight = 0
		total_chargeable_weight = 0
		max_length = 0
		max_width = 0
		max_height = 0

		for parcel in shipment:
			count = parcel.get("count", 1)
			total_pieces += count
			actual_weight = parcel["weight"] * count
			volumetric_weight = (parcel["length"] * parcel["width"] * parcel["height"]) / 5000
			chargeable_weight = max(actual_weight, volumetric_weight, 0.5)  # min 0.5 kg

			total_actual_weight += actual_weight
			total_chargeable_weight += chargeable_weight

			# Track max dimensions for the largest parcel
			max_length = max(max_length, parcel["length"])
			max_width = max(max_width, parcel["width"])
			max_height = max(max_height, parcel["height"])

		return {
			"Dimensions": {  # optional
				"Length": max_length,
				"Width": max_width,
				"Height": max_height,
				"Unit": "CM",
			},
			"NumberOfPieces": total_pieces,  # mandatory
			"ActualWeight": {  # mandatory
				"Unit": "KG",
				"Value": total_actual_weight,
			},
			"ChargeableWeight": {  # mandatory
				"Unit": "KG",
				"Value": total_chargeable_weight,
			},
			"ProductGroup": "EXP",  # mandatory
			"ProductType": "PDX",  # mandatory
			"PaymentType": "P",  # mandatory
			"PaymentOptions": "",  # conditional (based on payment type)
			"Services": "",  # optional
			"DescriptionOfGoods": "Documents",  # mandatory
			"GoodsOriginCountry": from_country,  # mandatory
			"Customs Value amount": "",  # conditional (based on payment type - Dutible)
			"Cash on Delivery": 0,  # conditional (based on service - COD)
			"Insurance Amount": 0,
			"Cash Additional Amount": 0,
			"Cash Additional Description": "",  # conditional (based on payment type - 3 && cash additional amount filled)
			"Collect Amount": 0,  # conditional (based on payment type - c && payment options - ARCC)
			"Items": [],  # optional
		}

	def get_service_dict(self, service):
		return frappe._dict(
			service_provider=ARAMEX_PROVIDER,
			service_name=service.get("Name"),
			carrier=service.get("Carrier"),
			total_price=service.get("Amount"),
			currency=service.get("CurrencyCode"),
		)


def get_aramex1_utils() -> "Aramex1Utils":
	settings = frappe.get_single("Aramex1")
	if not settings.enabled:
		link = get_link_to_form("Aramex1", "Aramex1", frappe.bold("Aramex1 Settings"))
		frappe.throw(_(f"Please enable Aramex1 Integration in {link}"), title=_("Mandatory"))

	return Aramex1Utils(
		base_url=TEST_BASE_URL if settings.use_test_environment else PROD_BASE_URL,
		username=settings.api_username,
		password=settings.get_password("api_password"),
		account_number=settings.account_number,
	)
